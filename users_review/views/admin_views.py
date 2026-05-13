"""
admin_views.py
---------------
어드민 관련 view 통합 모음

통합 대상 (기존 파일 → 클래스):
  admin_account_view.py    → AdminAccountListView
  admin_detail_view.py     → AdminAccountView (상세조회 + 수정 + 삭제)
  admin_permission_view.py → AdminPermissionView
"""

from typing import Any, Never, NoReturn

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions, status
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.utils.permissions import IsRoleAdminUser
from apps.users.serializers.admin_account_serializer import (
    AdminAccountListResponseSerializer,
    AdminAccountQuerySerializer,
)
from apps.users.serializers.admin_detail_serializer import AdminAccountDetailSerializer
from apps.users.serializers.admin_permission_serializer import AdminPermissionSerializer
from apps.users.serializers.admin_update_serializer import (
    AdminAccountUpdateResponseSerializer,
    AdminAccountUpdateSerializer,
)
from apps.users.services.admin_account_service import AdminAccountService
from apps.users.services.admin_delete_service import AdminAccountDeleteService
from apps.users.services.admin_detail_service import AdminAccountDetailService
from apps.users.services.admin_permission_service import update_user_role
from apps.users.services.admin_update_service import AdminAccountUpdateService
from apps.users.utils.admin_exceptions import AdminAccountException
from apps.users.utils.user_exceptions import UserNotFoundError


# ─── 어드민 회원 목록 ────────────────────────────────────────────────────────

class AdminAccountListView(APIView):
    permission_classes = [IsRoleAdminUser]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> NoReturn:
        if request.authenticators and not request.successful_authenticator:
            raise exceptions.NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")
        raise exceptions.PermissionDenied("권한이 없습니다.")

    @extend_schema(
        tags=["admin_accounts"],
        summary="어드민 회원 목록 조회",
        description="어드민 전용 회원 목록 조회 API. 검색, 상태/역할 필터, 페이지네이션을 지원합니다.",
        parameters=[
            OpenApiParameter(name="page", type=int, description="페이지 번호 (기본값: 1)", required=False),
            OpenApiParameter(name="page_size", type=int, description="페이지당 항목 수 (기본값: 10, 최대: 100)", required=False),
            OpenApiParameter(name="search", type=str, description="이메일 또는 닉네임으로 검색", required=False),
            OpenApiParameter(name="status", type=str, enum=["active", "inactive", "withdrew"], description="회원 상태 필터", required=False),
            OpenApiParameter(name="role", type=str, enum=["user", "admin", "student", "staff"], description="회원 역할 필터", required=False),
        ],
        responses={
            200: OpenApiResponse(description="어드민 회원목록 조회를 성공했습니다."),
            400: OpenApiResponse(description="유효하지 않은 요청 파라미터입니다."),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
            403: OpenApiResponse(description="권한이 없습니다."),
        },
    )
    def get(self, request: Request) -> Response:
        query_serializer = AdminAccountQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return Response(
                {"error_detail": "유효하지 않은 요청 파라미터입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = AdminAccountService.get_account_list(
            validated_params=query_serializer.validated_data,
            base_url=request.build_absolute_uri(request.path),
            query_params=request.query_params,
        )

        response_serializer = AdminAccountListResponseSerializer(data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


# ─── 어드민 회원 상세 / 수정 / 삭제 ──────────────────────────────────────────

class AdminAccountView(APIView):
    """
    GET    /accounts/{account_id} → 회원 상세 조회
    PATCH  /accounts/{account_id} → 회원 정보 수정
    DELETE /accounts/{account_id} → 회원 삭제
    """

    permission_classes = [IsRoleAdminUser]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> NoReturn:
        if request.authenticators and not request.successful_authenticator:
            raise exceptions.NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")
        raise exceptions.PermissionDenied("권한이 없습니다.")

    @extend_schema(
        tags=["admin_accounts"],
        summary="어드민 회원 상세 조회",
        description="어드민 전용 특정 회원의 상세 정보를 조회하는 API입니다.",
        responses={
            200: OpenApiResponse(description="어드민 회원 정보 상세 조회를 성공했습니다."),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
            403: OpenApiResponse(description="권한이 없습니다."),
            404: OpenApiResponse(description="사용자 정보를 찾을 수 없습니다."),
        },
    )
    def get(self, request: Request, account_id: int) -> Response:
        try:
            user = AdminAccountDetailService.get_account_detail(account_id)
        except AdminAccountException as exc:
            return Response({"error_detail": exc.detail}, status=exc.status_code)

        serializer = AdminAccountDetailSerializer(user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["admin_accounts"],
        summary="어드민 회원 정보 수정",
        description="어드민 전용 특정 회원의 정보를 수정하는 API입니다.",
        request=AdminAccountUpdateSerializer,
        responses={
            200: OpenApiResponse(description="어드민 회원 수정을 성공했습니다."),
            400: OpenApiResponse(description="11자리 숫자로 구성된 포맷이어야 합니다."),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
            403: OpenApiResponse(description="권한이 없습니다."),
            404: OpenApiResponse(description="사용자 정보를 찾을 수 없습니다."),
            409: OpenApiResponse(description="휴대폰 번호 중복으로 인하여 요청 처리에 실패하였습니다."),
        },
    )
    def patch(self, request: Request, account_id: int) -> Response:
        req_serializer = AdminAccountUpdateSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response({"error_detail": req_serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = AdminAccountUpdateService.update_account(account_id, req_serializer.validated_data)
        except AdminAccountException as exc:
            return Response({"error_detail": exc.detail}, status=exc.status_code)

        res_serializer = AdminAccountUpdateResponseSerializer(user)
        return Response(res_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["admin_accounts"],
        summary="어드민 회원 삭제",
        description="어드민 전용 특정 회원을 삭제하는 API입니다.",
        responses={
            200: OpenApiResponse(description="유저 데이터가 삭제되었습니다."),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
            403: OpenApiResponse(description="권한이 없습니다."),
            404: OpenApiResponse(description="사용자 정보를 찾을 수 없습니다."),
        },
    )
    def delete(self, request: Request, account_id: int) -> Response:
        try:
            pk = AdminAccountDeleteService.delete_account(account_id)
        except AdminAccountException as exc:
            return Response({"error_detail": exc.detail}, status=exc.status_code)

        return Response({"detail": f"유저 데이터가 삭제되었습니다. - pk: {pk}"}, status=status.HTTP_200_OK)


# ─── 어드민 권한 변경 ────────────────────────────────────────────────────────

class AdminPermissionView(APIView):
    permission_classes = [IsRoleAdminUser]

    def permission_denied(self, request: Request, message: str | None = None, code: str | None = None) -> Never:
        if not request.user.is_authenticated:
            raise NotAuthenticated("자격 인증 데이터가 제공되지 않았습니다.")
        raise PermissionDenied("권한이 없습니다.")

    @extend_schema(
        tags=["admin_accounts"],
        summary="어드민 페이지 권한 변경 API",
        description="관리자 권한을 가진 유저는 특정 유저 권한 변경 가능.",
        request=AdminPermissionSerializer,
        responses={
            200: OpenApiResponse(description="권한이 변경되었습니다."),
            400: OpenApiResponse(description="role로 권한 변경 시 필수 필드입니다."),
            401: OpenApiResponse(description="자격 인증 데이터가 제공되지 않았습니다."),
            403: OpenApiResponse(description="권한이 없습니다."),
            404: OpenApiResponse(description="사용자 정보를 찾을 수 없습니다."),
        },
    )
    def patch(self, request: Request, account_id: int) -> Response:
        serializer = AdminPermissionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error_detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            update_user_role(account_id, serializer.validated_data)
        except UserNotFoundError as e:
            return Response({"error_detail": str(e)}, status=status.HTTP_404_NOT_FOUND)

        return Response({"detail": "권한이 변경되었습니다."}, status=status.HTTP_200_OK)
