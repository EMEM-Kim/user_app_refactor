"""
auth_views.py
--------------
인증 관련 view 통합 모음

통합 대상 (기존 파일 → 클래스):
  user_signup_view.py   → SignupView
  user_login_view.py    → LoginView, LogoutView, TokenRefreshView
  auth_email_view.py    → EmailSendView, EmailVerificationView
  auth_sms_view.py      → SmsSendView, SmsVerificationView
  find_email_view.py    → FindEmailView
  find_password_view.py → FindPasswordView
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from django.conf import settings
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import User
from apps.users.serializers.auth_email_serializer import (
    EmailRequestSerializer,
    EmailVerifySerializer,
)
from apps.users.serializers.find_email_serializer import (
    FindEmailResponseSerializer,
    FindEmailSerializer,
)
from apps.users.serializers.find_password_serializer import FindPasswordSerializer
from apps.users.serializers.auth_sms_serializer import SmsSendSerializer, SmsVerifySerializer
from apps.users.serializers.user_login_serializer import LoginSerializer
from apps.users.serializers.user_signup_serializer import SignupSerializer
from apps.users.services.auth_email_service import EmailVerificationService
from apps.users.services.auth_sms_service import SmsVerificationService
from apps.users.services.find_email_service import find_email_service
from apps.users.services.find_password_service import find_password_service
from apps.users.services.user_login_service import UserLoginService
from apps.users.services.user_signup_service import create_user
from apps.users.utils.purpose_enum import AuthPurpose, SmsPurpose
from apps.users.utils.user_exceptions import ConflictError, InactiveError, InvalidLoginError, WithdrawnError


# ─── 회원가입 ────────────────────────────────────────────────────────────────

class SignupView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="회원가입",
        description="새로운 유저를 생성합니다. 이메일 중복 시 409 에러를 반환합니다.",
        request=SignupSerializer,
        responses={
            201: OpenApiExample("성공 응답", value={"detail": "회원가입이 완료되었습니다."}, response_only=True),
            400: OpenApiExample("유효성 검사 실패", value={"error_detail": {"email": ["이 필드는 필수 항목입니다."]}}, response_only=True),
            409: OpenApiExample("중복 데이터 충돌", value={"error_detail": "이미 가입된 이메일입니다."}, response_only=True),
        },
        tags=["Users"],
    )
    def post(self, request: Request) -> Response:
        serializer = SignupSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        try:
            create_user(serializer.validated_data)
            return Response({"detail": "회원가입이 완료되었습니다."}, status=status.HTTP_201_CREATED)
        except ConflictError as e:
            return Response({"error_detail": str(e)}, status=status.HTTP_409_CONFLICT)
        except ValidationError as e:
            return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)


# ─── 로그인 / 로그아웃 / 토큰 재발급 ─────────────────────────────────────────

class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Account(로그인)"],
        summary="이메일 로그인 API",
        description="이메일과 비밀번호로 로그인합니다. Access 토큰은 바디로, Refresh 토큰은 쿠키로 반환됩니다.",
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="로그인 성공"),
            400: OpenApiResponse(description="유효성 검사 실패"),
            403: OpenApiResponse(description="비활성화 계정, 탈퇴 계정, 로그인 정보 불일치"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data.get("email")
        password = serializer.validated_data.get("password")

        try:
            user = UserLoginService.verify_user(email, password)
        except WithdrawnError as e:
            return Response(
                {"error_detail": {"detail": str(e), "expire_at": e.expire_at}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except (InvalidLoginError, InactiveError) as e:
            return Response({"error_detail": str(e)}, status=status.HTTP_403_FORBIDDEN)

        access_token, refresh_token = UserLoginService.generate_token_pair(user)
        response = Response({"access_token": access_token}, status=status.HTTP_200_OK)
        response.set_cookie(
            key="refresh_token", value=refresh_token, httponly=True, secure=True, samesite="None", path="/"
        )
        return response


class LogoutView(APIView):
    permission_classes: list[Any] = []

    @extend_schema(
        tags=["Account(로그인)"],
        summary="로그아웃 API",
        request=None,
        responses={200: OpenApiResponse(description="로그아웃 성공")},
    )
    def post(self, request: Request) -> Response:
        refresh_token = request.COOKIES.get("refresh_token")
        if refresh_token:
            UserLoginService.add_to_blacklist(refresh_token)

        response = Response({"detail": "성공적으로 로그아웃 되었습니다."}, status=status.HTTP_200_OK)
        response.delete_cookie("refresh_token")
        return response


class TokenRefreshView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Account(로그인)"],
        summary="JWT 토큰 재발급 API",
        description="HttpOnly 쿠키의 refresh_token으로 새 access_token 발급. refresh_token도 갱신됩니다.",
        request=None,
        responses={
            200: OpenApiResponse(description="토큰 재발급 성공"),
            400: OpenApiResponse(description="refresh 쿠키 없음"),
            403: OpenApiResponse(description="유효하지 않은 토큰"),
        },
    )
    def post(self, request: Request) -> Response:
        valid_refresh_token = request.COOKIES.get("refresh_token")
        if not valid_refresh_token:
            return Response({"error_detail": "refresh_token 쿠키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if UserLoginService.is_blacklisted(valid_refresh_token):
                return Response({"error_detail": "로그인 세션이 만료되었습니다."}, status=status.HTTP_403_FORBIDDEN)

            old_refresh = RefreshToken(valid_refresh_token)  # type: ignore[arg-type]
            user_id = old_refresh.payload.get("user_id")
            user = User.objects.get(id=user_id)

            new_access, new_refresh = UserLoginService.generate_token_pair(user)
            UserLoginService.add_to_blacklist(valid_refresh_token)

        except (TokenError, User.DoesNotExist):
            return Response({"error_detail": "로그인 세션이 만료되었습니다."}, status=status.HTTP_403_FORBIDDEN)

        response = Response({"access_token": new_access}, status=status.HTTP_200_OK)
        refresh_lifetime = cast(timedelta, settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"])
        response.set_cookie(
            key="refresh_token",
            value=new_refresh,
            httponly=True,
            secure=True,
            samesite="None",
            path="/",
            max_age=int(refresh_lifetime.total_seconds()),
        )
        return response


# ─── 이메일 인증 ─────────────────────────────────────────────────────────────

class EmailSendView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Accounts (이메일 인증)"],
        summary="이메일 인증 코드 발송 API",
        description="회원가입, 비밀번호 찾기, 계정 복구 등 용도(purpose)에 맞는 6자리 이메일 인증 코드를 발송합니다.",
        request=EmailRequestSerializer,
        responses={
            200: OpenApiResponse(description="발송 성공"),
            400: OpenApiResponse(description="잘못된 요청"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = EmailRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"]
        purpose = AuthPurpose(serializer.validated_data["purpose"])

        try:
            EmailVerificationService.send_verification_email(email, purpose)
            return Response({"detail": "이메일 인증 코드가 전송되었습니다"}, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)


class EmailVerificationView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Accounts (이메일 인증)"],
        summary="이메일 인증 코드 검증 API",
        description="사용자가 입력한 6자리 인증 코드를 검증하고, 성공 시 email_token을 반환합니다.",
        request=EmailVerifySerializer,
        responses={
            200: OpenApiResponse(description="검증 성공"),
            400: OpenApiResponse(description="코드 불일치 또는 만료"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = EmailVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"]
        code = serializer.validated_data["code"]
        purpose = AuthPurpose(serializer.validated_data["purpose"])

        try:
            email_token = EmailVerificationService.verification_code(email, code, purpose)
            return Response(
                {"detail": "이메일 인증에 성공하였습니다", "email_token": email_token},
                status=status.HTTP_200_OK,
            )
        except ValidationError as e:
            return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)


# ─── SMS 인증 ────────────────────────────────────────────────────────────────

class SmsSendView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Accounts (sms 인증)"],
        summary="sms 인증 코드 발송 API",
        description="회원가입, 이메일 찾기, 전화번호 변경 등 용도(purpose)에 맞는 6자리 sms 인증 코드를 발송합니다.",
        request=SmsSendSerializer,
        responses={
            200: OpenApiResponse(description="발송 성공"),
            400: OpenApiResponse(description="잘못된 요청"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = SmsSendSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data["phone_number"]
        purpose = SmsPurpose(serializer.validated_data["purpose"])

        try:
            SmsVerificationService.send_verification_sms(phone_number, purpose)
            return Response({"message": "회원가입을 위한 휴대폰 인증 코드가 전송되었습니다"})
        except ValidationError as e:
            return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)


class SmsVerificationView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Accounts (sms 인증)"],
        summary="sms 인증 코드 검증 API",
        description="사용자가 입력한 6자리 인증 코드를 검증하고, 성공 시 sms_token을 반환합니다.",
        request=SmsVerifySerializer,
        responses={
            200: OpenApiResponse(description="검증 성공"),
            400: OpenApiResponse(description="코드 불일치 또는 만료"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = SmsVerifySerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            phone_number = serializer.validated_data["phone_number"]
            code = serializer.validated_data["code"]
            purpose = SmsPurpose(serializer.validated_data["purpose"])

            sms_token = SmsVerificationService.verify_sms_code(phone_number, code, purpose)
            return Response({"message": "회원가입을 위한 휴대폰 인증에 성공했습니다.", "sms_token": sms_token})
        except ValidationError as e:
            return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)


# ─── 이메일 / 비밀번호 찾기 ──────────────────────────────────────────────────

class FindEmailView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["accounts"],
        summary="이메일 찾기 api",
        description="SMS 인증 후 발급받은 토큰을 활용하여 이메일 찾기",
        request=FindEmailSerializer,
        responses={
            200: OpenApiResponse(description="이메일 찾기 성공"),
            400: OpenApiResponse(description="유효성 검사 실패"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = FindEmailSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            masked_email = find_email_service(serializer.validated_data)
            response_serializer = FindEmailResponseSerializer({"email": masked_email})
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)


class FindPasswordView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["accounts"],
        summary="비밀번호 분실시 재설정 api",
        description="이메일 인증 후 발급받은 토큰을 활용하여 유저 비밀번호 재설정",
        request=FindPasswordSerializer,
        responses={
            200: OpenApiResponse(description="비밀번호 재설정 성공"),
            400: OpenApiResponse(description="유효성 검사 실패"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = FindPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            find_password_service(serializer.validated_data)
            return Response({"detail": "비밀번호 변경 성공."}, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)
