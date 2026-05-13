"""
profile_views.py
-----------------
프로필/계정 관리 관련 view 통합 모음

통합 대상 (기존 파일 → 클래스):
  user_info_view.py      → UserInfoView
  check_nickname_view.py → CheckNicknameView
  profile_image_views.py → ProfileImageView
  password_change_view.py → PasswordChangeView
  change_phone_view.py   → ChangePhoneView
  withdrawal_view.py     → WithdrawalView, RestoreView
"""

from __future__ import annotations

from typing import Never, cast

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import User
from apps.users.serializers.change_phone_serializer import ChangePhoneSerializer
from apps.users.serializers.check_nickname_serializer import CheckNicknameSerializer
from apps.users.serializers.password_change_serializer import PasswordChangeSerializer
from apps.users.serializers.profile_image_serializer import ProfileImageUpdateSerializer
from apps.users.serializers.user_info_serializer import (
    UserInfoSerializer,
    UserInfoUpdateResponseSerializer,
    UserInfoUpdateSerializer,
)
from apps.users.serializers.withdrawal_serializer import RestoreSerializer, WithdrawalSerializer
from apps.users.services.change_phone_service import change_phone_service
from apps.users.services.password_change_service import PasswordChangeService
from apps.users.services.profile_image_service import update_profile_image
from apps.users.services.withdrawal_service import restore_user_by_token, withdraw_user
from apps.users.utils.user_exceptions import ConflictError, DuplicateNicknameError
from apps.users.utils.withdrawal_exceptions import WithdrawalBadRequestError, WithdrawalNotFoundError


# ─── 내 정보 조회 / 수정 ─────────────────────────────────────────────────────

class UserInfoView(APIView):
    permission_classes = [IsAuthenticated]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> Never:
        raise NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")

    @extend_schema(
        tags=["accounts"],
        summary="내 정보 조회 API",
        description="로그인한 유저는 회원정보 조회 가능. 수강생은 수강 중 과정·기수도 조회 가능.",
        responses={
            200: UserInfoSerializer,
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
        },
    )
    def get(self, request: Request) -> Response:
        serializer = UserInfoSerializer(cast(User, request.user))
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Accounts (회원관리)"],
        summary="내 정보 수정 API",
        description="로그인한 유저는 닉네임, 이름, 생년월일, 성별 수정 가능.",
        request=UserInfoUpdateSerializer,
        responses={
            200: UserInfoUpdateResponseSerializer,
            400: OpenApiResponse(description="잘못된 요청"),
            401: OpenApiResponse(description="인증 실패"),
            409: OpenApiResponse(description="중복된 닉네임"),
        },
    )
    def patch(self, request: Request) -> Response:
        user = cast(User, request.user)
        serializer = UserInfoUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        response_serializer = UserInfoUpdateResponseSerializer(user)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


# ─── 닉네임 중복 확인 ────────────────────────────────────────────────────────

class CheckNicknameView(APIView):
    # 회원가입(로그인 전), 내 정보 수정(로그인 후) 양쪽에서 사용되므로 AllowAny
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["accounts"],
        summary="닉네임 중복 확인 API",
        request=CheckNicknameSerializer,
        responses={
            200: CheckNicknameSerializer,
            400: OpenApiResponse(description="이 필드는 필수 항목입니다."),
            409: OpenApiResponse(description="중복된 닉네임이 존재합니다."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = CheckNicknameSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            return Response({"detail": "사용 가능한 닉네임입니다."}, status=status.HTTP_200_OK)
        except DuplicateNicknameError as e:
            return Response({"error_detail": str(e)}, status=status.HTTP_409_CONFLICT)
        except ValidationError:
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


# ─── 프로필 이미지 ───────────────────────────────────────────────────────────

class ProfileImageView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["accounts"], summary="프로필 이미지 URL 저장")
    def patch(self, request: Request) -> Response:
        serializer = ProfileImageUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        user: User = request.user  # type: ignore[assignment]
        update_profile_image(user, serializer.validated_data["profile_img_url"])
        return Response({"detail": "프로필 사진이 등록되었습니다."}, status=status.HTTP_200_OK)


# ─── 비밀번호 변경 ───────────────────────────────────────────────────────────

class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> Never:
        raise NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")

    @extend_schema(
        tags=["accounts"],
        summary="비밀번호 재설정 API",
        description="로그인한 유저의 비밀번호를 변경하는 API입니다.",
        request=PasswordChangeSerializer,
        responses={
            200: OpenApiResponse(description="비밀번호 변경 성공."),
            400: OpenApiResponse(description="잘못된 요청"),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        user = cast(User, request.user)
        old_password: str = serializer.validated_data["old_password"]
        new_password: str = serializer.validated_data["new_password"]

        try:
            PasswordChangeService.change_password(user, old_password, new_password)
        except ValueError as e:
            return Response(
                {"error_detail": {"old_password": [str(e)]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"detail": "비밀번호 변경 성공."}, status=status.HTTP_200_OK)


# ─── 휴대폰 번호 변경 ────────────────────────────────────────────────────────

class ChangePhoneView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["accounts"],
        summary="휴대폰 번호 변경 api",
        description="SMS 인증 후 발급받은 토큰을 활용하여 휴대폰 번호 변경.",
        request=ChangePhoneSerializer,
        responses={
            200: OpenApiResponse(description="휴대폰 번호 변경 성공"),
            400: OpenApiResponse(description="유효성 검사 실패"),
            401: OpenApiResponse(description="자격 인증 실패"),
            409: OpenApiResponse(description="이미 등록된 휴대폰 번호"),
        },
    )
    def patch(self, request: Request) -> Response:
        serializer = ChangePhoneSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        try:
            user = request.user
            assert isinstance(user, User)
            new_phone_number = change_phone_service(serializer.validated_data, user)
            return Response(
                {"detail": "휴대폰 번호 변경에 성공하였습니다.", "phone_number": new_phone_number},
                status=status.HTTP_200_OK,
            )
        except ConflictError as e:
            return Response({"error_detail": str(e)}, status=status.HTTP_409_CONFLICT)
        except ValidationError as e:
            return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)


# ─── 탈퇴 / 계정 복구 ────────────────────────────────────────────────────────

class WithdrawalView(APIView):
    """
    DELETE: 회원 탈퇴
    ※ urls.py 에서 UserInfoView 보다 먼저 include되어 'me' 경로가 우선 매칭됩니다.
    """

    permission_classes = [IsAuthenticated]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> Never:
        if request.authenticators and not request.successful_authenticator:
            raise NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")
        super().permission_denied(request, message, code)  # type: ignore[misc]

    @extend_schema(
        tags=["accounts"],
        summary="회원 탈퇴",
        description="탈퇴 신청 후 2주간 데이터가 보관되며, 2주 내 계정 복구가 가능합니다.",
        request=WithdrawalSerializer(),
        responses={
            204: OpenApiResponse(description="탈퇴 처리 완료"),
            400: inline_serializer(
                name="WithdrawalValidationError",
                fields={"error_detail": serializers.CharField(required=False)},
            ),
            401: inline_serializer(
                name="WithdrawalUnauthorized",
                fields={"error_detail": serializers.CharField()},
            ),
        },
    )
    def delete(self, request: Request) -> Response:
        serializer = WithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            withdraw_user(
                user=cast(User, request.user),
                reason=serializer.validated_data["reason"],
                reason_detail=serializer.validated_data["reason_detail"],
            )
        except WithdrawalBadRequestError as e:
            return Response({"error_detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class RestoreView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["accounts"],
        summary="계정 복구",
        description="이메일 인증 후 발급된 email_token으로 계정을 복구합니다. 토큰은 10분간 유효합니다.",
        request=RestoreSerializer,
        responses={
            200: OpenApiResponse(description="계정 복구 완료"),
            400: inline_serializer(
                name="RestoreValidationError",
                fields={"error_detail": serializers.CharField()},
            ),
            404: inline_serializer(
                name="RestoreNotFound",
                fields={"error_detail": serializers.CharField()},
            ),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = RestoreSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            restore_user_by_token(serializer.validated_data["email_token"])
        except WithdrawalBadRequestError as e:
            return Response({"error_detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except WithdrawalNotFoundError as e:
            return Response({"error_detail": str(e)}, status=status.HTTP_404_NOT_FOUND)

        return Response({"detail": "계정복구가 완료되었습니다."}, status=status.HTTP_200_OK)
