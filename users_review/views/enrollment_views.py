"""
enrollment_views.py
--------------------
수강신청 관련 view 통합 모음

통합 대상 (기존 파일 → 클래스):
  enrollment_view.py               → EnrollmentView
  enrolled_courses_view.py         → MyCoursesView
  available_courses_view.py        → AvailableCoursesView
  enrollment_accept_view.py        → AdminStudentEnrollmentAcceptView
  admin_student_enrollment_reject_view.py → AdminStudentEnrollmentRejectView
"""

from __future__ import annotations

from typing import Never, NoReturn, cast

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions, status
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.utils.permissions import IsRoleAdminUser, IsStudentUser
from apps.users.models import User
from apps.users.serializers.available_courses_serializer import AvailableCoursesSerializer
from apps.users.serializers.enrolled_courses_serializer import MyCoursesSerializer
from apps.users.serializers.enrollment_accept_serializer import AdminEnrollmentAcceptSerializer
from apps.users.serializers.enrollment_serializer import EnrollmentSerializer
from apps.users.serializers.admin_student_enrollment_reject_serializer import (
    AdminStudentEnrollmentErrorSerializer,
    AdminStudentEnrollmentRejectRequestSerializer,
    AdminStudentEnrollmentRejectResponseSerializer,
    AdminStudentEnrollmentValidationErrorSerializer,
)
from apps.users.services.available_courses_service import get_available_cohorts
from apps.users.services.enrolled_courses_service import get_my_courses
from apps.users.services.enrollment_accept_service import (
    AdminEnrollmentAcceptService,
    EnrollmentAcceptError,
)
from apps.users.services.enrollment_service import create_enrollment
from apps.users.services.admin_student_enrollment_reject_service import reject_student_enrollments


# ─── 수강신청 ────────────────────────────────────────────────────────────────

class EnrollmentView(APIView):
    permission_classes = [IsAuthenticated]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> Never:
        raise NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")

    @extend_schema(
        tags=["accounts"],
        summary="수강생 등록 신청 API",
        description="로그인한 유저만 과정과 기수를 선택하여 수강생 등록 신청할 수 있습니다.",
        request=EnrollmentSerializer,
        responses={
            201: OpenApiResponse(description="수강생 등록 신청완료."),
            400: OpenApiResponse(description="이 필드는 필수 항목입니다."),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
        },
    )
    def post(self, request: Request) -> Response:
        user = request.user
        serializer = EnrollmentSerializer(data=request.data)
        if serializer.is_valid():
            try:
                create_enrollment(user=cast(User, user), validated_data=serializer.validated_data)
                return Response({"detail": "수강생 등록 신청완료."}, status=status.HTTP_201_CREATED)
            except ValidationError as e:
                return Response({"error_detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


# ─── 내 수강목록 조회 ─────────────────────────────────────────────────────────

class MyCoursesView(APIView):
    permission_classes = [IsStudentUser]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> Never:
        if not request.user.is_authenticated:
            raise NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")
        raise PermissionDenied("수강 목록 조회 권한이 없습니다.")

    @extend_schema(
        tags=["accounts"],
        summary="내 수강목록 조회 API",
        description="로그인한 유저의 수강목록만 조회.",
        responses={
            200: MyCoursesSerializer(many=True),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
            403: OpenApiResponse(description="수강 목록 조회 권한이 없습니다."),
        },
    )
    def get(self, request: Request) -> Response:
        user = cast(User, request.user)
        data = get_my_courses(user)
        serializer = MyCoursesSerializer(data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ─── 수강신청 가능 기수 조회 ──────────────────────────────────────────────────

class AvailableCoursesView(APIView):
    permission_classes = [IsAuthenticated]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> Never:
        raise NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")

    @extend_schema(
        tags=["accounts"],
        summary="수강신청 가능한 기수 조회 API",
        description="모집 중인 기수만 소속된 과정 정보와 함께 반환합니다.",
        responses={
            200: AvailableCoursesSerializer,
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
        },
    )
    def get(self, request: Request) -> Response:
        user = cast(User, request.user)
        data = get_available_cohorts(user)
        serializer = AvailableCoursesSerializer(data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ─── 어드민 수강생 등록 승인 ──────────────────────────────────────────────────

class AdminStudentEnrollmentAcceptView(APIView):
    permission_classes = [IsRoleAdminUser]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> NoReturn:
        if request.authenticators and not request.successful_authenticator:
            raise exceptions.NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")
        raise exceptions.PermissionDenied("권한이 없습니다.")

    @extend_schema(
        tags=["admin_students"],
        summary="수강생 등록 요청 승인",
        description="어드민 페이지 수강생 등록 요청 승인 API",
        request=AdminEnrollmentAcceptSerializer,
        responses={
            200: OpenApiResponse(description="수강생 등록 신청들에 대한 승인 요청이 처리되었습니다."),
            400: OpenApiResponse(description="이 필드는 필수 항목입니다."),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
            403: OpenApiResponse(description="권한이 없습니다."),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = AdminEnrollmentAcceptSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            AdminEnrollmentAcceptService.accept_enrollments(serializer.validated_data["enrollments"])
        except EnrollmentAcceptError as exc:
            return Response(
                {"error_detail": f"처리할 수 없는 등록 요청 ID가 포함되어 있습니다: {exc.invalid_ids}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": "수강생 등록 신청들에 대한 승인 요청이 처리되었습니다."},
            status=status.HTTP_200_OK,
        )


# ─── 어드민 수강생 등록 거절 ──────────────────────────────────────────────────

class AdminStudentEnrollmentRejectView(APIView):
    permission_classes = [IsRoleAdminUser]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> NoReturn:
        if not request.user.is_authenticated:
            raise NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")
        raise PermissionDenied("권한이 없습니다.")

    @extend_schema(
        tags=["admin_students"],
        summary="어드민 수강생 등록 요청 거절",
        description="어드민이 수강생 등록 신청들에 대한 거절 요청을 처리합니다.",
        request=AdminStudentEnrollmentRejectRequestSerializer,
        responses={
            200: AdminStudentEnrollmentRejectResponseSerializer,
            400: OpenApiResponse(description="잘못된 요청", response=AdminStudentEnrollmentValidationErrorSerializer),
            401: OpenApiResponse(description="인증 실패", response=AdminStudentEnrollmentErrorSerializer),
            403: OpenApiResponse(description="권한 없음", response=AdminStudentEnrollmentErrorSerializer),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = AdminStudentEnrollmentRejectRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        reject_student_enrollments(serializer.validated_data["enrollments"])
        return Response(
            {"detail": "수강생 등록 신청들에 대한 거절 요청이 처리되었습니다."},
            status=status.HTTP_200_OK,
        )
