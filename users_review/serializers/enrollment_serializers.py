"""
enrollment_serializers.py
--------------------------
수강신청 관련 serializer 통합 모음

통합 대상 (기존 파일 → 클래스):
  enrollment_serializer.py        → EnrollmentSerializer
  enrollment_accept_serializer.py → AdminEnrollmentAcceptSerializer
  enrolled_courses_serializer.py  → MyCohortInfoSerializer, MyCourseInfoSerializer, MyCoursesSerializer
  available_courses_serializer.py → CohortInfoSerializer, CourseInfoSerializer, AvailableCoursesSerializer
"""

from typing import Any

from rest_framework import serializers

from apps.courses.models.cohort import Cohort
from apps.posts.models.course import Course


# ─── 수강신청 ────────────────────────────────────────────────────────────────

class EnrollmentSerializer(serializers.Serializer["EnrollmentSerializer"]):
    cohort_id = serializers.IntegerField()

    def validate_cohort_id(self, value: int) -> int:
        if not Cohort.objects.filter(id=value).exists():
            raise serializers.ValidationError("존재하지 않는 기수입니다.")
        return value


# ─── 어드민 수강생 등록 승인 ──────────────────────────────────────────────────

class AdminEnrollmentAcceptSerializer(serializers.Serializer[Any]):
    enrollments = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        allow_empty=False,
    )


# ─── 어드민 수강생 등록 거절 ──────────────────────────────────────────────────

class AdminStudentEnrollmentRejectRequestSerializer(serializers.Serializer[Any]):
    enrollments = serializers.ListField(child=serializers.IntegerField(), required=True)


class AdminStudentEnrollmentRejectResponseSerializer(serializers.Serializer[Any]):
    detail = serializers.CharField(read_only=True)


class AdminStudentEnrollmentErrorSerializer(serializers.Serializer[Any]):
    error_detail = serializers.CharField(read_only=True)


class AdminStudentEnrollmentValidationErrorSerializer(serializers.Serializer[Any]):
    error_detail = serializers.DictField(child=serializers.ListField(child=serializers.CharField()), read_only=True)


# ─── 내 수강목록 조회 ─────────────────────────────────────────────────────────

class MyCohortInfoSerializer(serializers.ModelSerializer[Cohort]):
    class Meta:
        model = Cohort
        fields = ["id", "number", "start_date", "end_date", "status"]
        read_only_fields = fields


class MyCourseInfoSerializer(serializers.ModelSerializer[Course]):
    class Meta:
        model = Course
        fields = ["id", "name", "tag", "thumbnail_img_url"]
        read_only_fields = fields


class MyCoursesSerializer(serializers.Serializer[Any]):
    """내 수강목록 메인 serializer (cohort + course 묶음)"""
    cohort = MyCohortInfoSerializer(source="*", read_only=True)
    course = MyCourseInfoSerializer(read_only=True)


# ─── 수강신청 가능한 기수 조회 ────────────────────────────────────────────────

class CohortInfoSerializer(serializers.ModelSerializer[Cohort]):
    class Meta:
        model = Cohort
        fields = ["id", "number", "start_date", "end_date", "status"]
        read_only_fields = fields


class CourseInfoSerializer(serializers.ModelSerializer[Course]):
    class Meta:
        model = Course
        fields = ["id", "name"]
        read_only_fields = fields


class AvailableCoursesSerializer(serializers.Serializer[Any]):
    """수강신청 가능 기수 메인 serializer (cohort + course 묶음)"""
    cohort = CohortInfoSerializer(source="*", read_only=True)
    course = CourseInfoSerializer(read_only=True)
