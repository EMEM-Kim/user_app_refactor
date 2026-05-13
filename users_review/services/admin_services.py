"""
admin_services.py
------------------
어드민 관련 service 통합 모음

통합 대상 (기존 파일 → 클래스 / 함수):
  admin_account_service.py                 → AdminAccountService
  admin_detail_service.py                  → AdminAccountDetailService
  admin_update_service.py                  → AdminAccountUpdateService
  admin_delete_service.py                  → AdminAccountDeleteService
  admin_permission_service.py              → update_user_role
  admin_student_enrollment_reject_service.py → reject_student_enrollments
    ※ reject_student_enrollments 는 enrollment_services.py 에도 동일하게 존재합니다.
       어드민 서비스 파일에서도 직접 접근할 수 있도록 여기에도 포함했습니다.
"""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.http import QueryDict

from apps.users.models import (
    CohortStudents,
    LearningCoachs,
    OperationManagers,
    StudentEnrollmentRequests,
    TrainigAssistants,
    User,
)
from apps.users.utils.admin_exceptions import (
    AccountDuplicatePhoneError,
    AccountNotFoundError,
)
from apps.users.utils.user_exceptions import UserNotFoundError


# ─── 어드민 회원 목록 ────────────────────────────────────────────────────────

class AdminAccountService:

    @staticmethod
    def get_account_list(
        validated_params: dict[str, Any],
        base_url: str,
        query_params: QueryDict,
    ) -> dict[str, Any]:
        queryset: QuerySet[User] = User.objects.all().order_by("-created_at")

        if search := validated_params.get("search"):
            queryset = queryset.filter(Q(email__icontains=search) | Q(nickname__icontains=search))

        if status := validated_params.get("status"):
            if status == "active":
                queryset = queryset.filter(is_active=True)
            elif status == "inactive":
                queryset = queryset.filter(is_active=False)
            elif status == "withdrew":
                queryset = queryset.filter(withdrawal__isnull=False)

        if role := validated_params.get("role"):
            queryset = queryset.filter(role=role.upper())

        page: int = validated_params.get("page", 1)
        page_size: int = validated_params.get("page_size", 10)
        offset: int = (page - 1) * page_size

        total_count: int = queryset.count()
        results = queryset[offset : offset + page_size]

        params = query_params.copy()
        params["page_size"] = str(page_size)

        params["page"] = str(page + 1)
        next_url = f"{base_url}?{params.urlencode()}" if (page * page_size) < total_count else None

        params["page"] = str(page - 1)
        previous_url = f"{base_url}?{params.urlencode()}" if page > 1 else None

        return {
            "count": total_count,
            "next": next_url,
            "previous": previous_url,
            "results": results,
        }


# ─── 어드민 회원 상세 조회 ────────────────────────────────────────────────────

class AdminAccountDetailService:

    @staticmethod
    def get_account_detail(account_id: int) -> User:
        try:
            return (
                User.objects.select_related("withdrawal")
                .prefetch_related("cohort_students__cohort__course")
                .get(pk=account_id)
            )
        except User.DoesNotExist:
            raise AccountNotFoundError()


# ─── 어드민 회원 정보 수정 ────────────────────────────────────────────────────

class AdminAccountUpdateService:

    @staticmethod
    def update_account(account_id: int, validated_data: dict[str, Any]) -> User:
        try:
            user = User.objects.get(pk=account_id)
        except User.DoesNotExist:
            raise AccountNotFoundError()

        for field, value in validated_data.items():
            setattr(user, field, value)

        try:
            user.save(update_fields=list(validated_data.keys()) + ["updated_at"])
        except IntegrityError:
            raise AccountDuplicatePhoneError()

        return user


# ─── 어드민 회원 삭제 ────────────────────────────────────────────────────────

class AdminAccountDeleteService:

    @staticmethod
    def delete_account(account_id: int) -> int:
        try:
            user = User.objects.get(pk=account_id)
        except User.DoesNotExist:
            raise AccountNotFoundError()

        pk = user.pk
        user.delete()
        return pk


# ─── 어드민 권한 변경 ────────────────────────────────────────────────────────

@transaction.atomic
def update_user_role(account_id: int, validated_data: dict[str, Any]) -> User:
    """
    기존 역할 관련 테이블 레코드를 삭제한 뒤 새 role에 맞는 레코드를 생성하고
    User.role 필드를 업데이트합니다.
    """
    try:
        user = User.objects.select_for_update().get(id=account_id)
    except User.DoesNotExist:
        raise UserNotFoundError()

    role = validated_data["role"]

    tables_to_clear: list[Any] = [CohortStudents, OperationManagers, LearningCoachs, TrainigAssistants]
    for table in tables_to_clear:
        table.objects.filter(user=user).delete()

    if role == "STUDENT":
        CohortStudents.objects.create(user=user, cohort_id=validated_data["cohort_id"])
    elif role == "TA":
        TrainigAssistants.objects.create(user=user, cohort_id=validated_data["cohort_id"])
    elif role == "OM":
        for course_id in validated_data["assigned_courses"]:
            OperationManagers.objects.create(user=user, course_id=course_id)
    elif role == "LC":
        for course_id in validated_data["assigned_courses"]:
            LearningCoachs.objects.create(user=user, course_id=course_id)

    user.role = role
    user.save()

    return user
