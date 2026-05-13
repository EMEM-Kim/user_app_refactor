"""
enrollment_services.py
-----------------------
수강신청 관련 service 통합 모음

통합 대상 (기존 파일 → 클래스 / 함수):
  enrollment_service.py        → create_enrollment
  enrollment_accept_service.py → EnrollmentAcceptError, AdminEnrollmentAcceptService
  enrolled_courses_service.py  → get_my_courses
  available_courses_service.py → get_available_cohorts
"""

from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.courses.models.cohort import Cohort, StatusChoices
from apps.users.models import CohortStudents, StudentEnrollmentRequests, User


# ─── 수강신청 ────────────────────────────────────────────────────────────────

@transaction.atomic
def create_enrollment(user: User, validated_data: dict[str, Any]) -> StudentEnrollmentRequests:
    """
    이미 PENDING 상태의 신청이 있으면 예외를 발생시키고,
    없으면 새로운 수강신청 레코드를 생성합니다.
    """
    cohort_id = validated_data.get("cohort_id")

    if (
        StudentEnrollmentRequests.objects.select_for_update()
        .filter(user=user, status=StudentEnrollmentRequests.Status.PENDING)
        .exists()
    ):
        raise ValidationError("이미 등록 신청중인 내역이 있습니다")

    return StudentEnrollmentRequests.objects.create(
        user=user,
        cohort_id=cohort_id,
        status=StudentEnrollmentRequests.Status.PENDING,
    )


# ─── 어드민 수강생 등록 승인 ──────────────────────────────────────────────────

class EnrollmentAcceptError(Exception):
    def __init__(self, invalid_ids: list[int]) -> None:
        self.invalid_ids = invalid_ids
        super().__init__(f"처리할 수 없는 등록 요청 ID가 포함되어 있습니다: {invalid_ids}")


class AdminEnrollmentAcceptService:

    @staticmethod
    @transaction.atomic
    def accept_enrollments(enrollment_ids: list[int]) -> None:
        enrollments = list(
            StudentEnrollmentRequests.objects.select_for_update()
            .filter(id__in=enrollment_ids, status=StudentEnrollmentRequests.Status.PENDING)
            .select_related("user")
        )

        found_ids = {e.id for e in enrollments}
        invalid_ids = [eid for eid in enrollment_ids if eid not in found_ids]
        if invalid_ids:
            raise EnrollmentAcceptError(invalid_ids)

        now = timezone.now()
        cohort_students = []
        user_ids = []

        for enrollment in enrollments:
            enrollment.status = StudentEnrollmentRequests.Status.ACCEPTED
            enrollment.accepted_at = now
            cohort_students.append(CohortStudents(user=enrollment.user, cohort=enrollment.cohort))
            user_ids.append(enrollment.user_id)

        StudentEnrollmentRequests.objects.bulk_update(enrollments, ["status", "accepted_at"])
        CohortStudents.objects.bulk_create(cohort_students)
        User.objects.filter(id__in=user_ids).update(role=User.Role.STUDENT)


# ─── 어드민 수강생 등록 거절 ──────────────────────────────────────────────────

def reject_student_enrollments(enrollment_ids: list[int]) -> None:
    with transaction.atomic():
        StudentEnrollmentRequests.objects.filter(id__in=enrollment_ids).update(
            status=StudentEnrollmentRequests.Status.REJECTED,
            accepted_at=None,
        )


# ─── 내 수강목록 조회 ─────────────────────────────────────────────────────────

def get_my_courses(user: User) -> QuerySet[Cohort]:
    """
    수강 신청하여 승인된(ACCEPTED) 기수 목록을 course 정보와 함께 반환합니다.
    현재 수강 중, 수강 예정, 수강 완료된 기수 전체를 포함합니다.
    """
    return (
        Cohort.objects.filter(
            studentenrollmentrequests__user=user,
            studentenrollmentrequests__status=StudentEnrollmentRequests.Status.ACCEPTED,
        )
        .select_related("course")
        .order_by("-start_date")
        .distinct()
    )


# ─── 수강신청 가능 기수 조회 ──────────────────────────────────────────────────

def get_available_cohorts(user: User) -> QuerySet[Cohort]:
    """
    모집 중(PREPARING / IN_PROGRESS) 기수 중 이미 신청 중이거나 수강 중인 기수를 제외하여
    course 정보와 함께 반환합니다.
    """
    excluded_ids = StudentEnrollmentRequests.objects.filter(
        user=user,
        status__in=[
            StudentEnrollmentRequests.Status.PENDING,
            StudentEnrollmentRequests.Status.ACCEPTED,
        ],
    ).values_list("cohort_id", flat=True)

    return (
        Cohort.objects.filter(status__in=[StatusChoices.PREPARING, StatusChoices.IN_PROGRESS])
        .exclude(id__in=excluded_ids)
        .select_related("course")
    )
