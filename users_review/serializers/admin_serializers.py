"""
admin_serializers.py
---------------------
어드민 관련 serializer 통합 모음

통합 대상 (기존 파일 → 클래스):
  admin_account_serializer.py                → AdminAccountQuerySerializer, AdminAccountSerializer, AdminAccountListResponseSerializer
  admin_detail_serializer.py                 → AdminCourseWithTagSerializer, AdminAssignedCourseSerializer, AdminAccountDetailSerializer
  admin_permission_serializer.py             → AdminPermissionSerializer
  admin_update_serializer.py                 → AdminAccountUpdateSerializer, AdminAccountUpdateResponseSerializer
"""

from __future__ import annotations

import re
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from apps.courses.models.cohort import Cohort
from apps.posts.models.course import Course
from apps.users.models import CohortStudents, User


# ─── 어드민 회원 목록 ────────────────────────────────────────────────────────

class AdminAccountQuerySerializer(serializers.Serializer[Any]):
    """어드민 회원 목록 조회 쿼리 파라미터"""
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    page_size = serializers.IntegerField(required=False, default=10, min_value=1, max_value=100)
    search = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=["active", "inactive", "withdrew"], required=False)
    role = serializers.ChoiceField(choices=["user", "admin", "student", "staff"], required=False)


class AdminAccountSerializer(serializers.ModelSerializer[User]):
    """어드민 회원 목록 단일 항목"""
    status = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    def get_status(self, obj: User) -> str:
        try:
            obj.withdrawal
            return "withdrew"
        except ObjectDoesNotExist:
            return "active" if obj.is_active else "inactive"

    def get_role(self, obj: User) -> str:
        return obj.role.lower()

    class Meta:
        model = User
        fields = ["id", "email", "nickname", "name", "birthday", "status", "role", "created_at"]


class AdminAccountListResponseSerializer(serializers.Serializer[Any]):
    """어드민 회원 목록 페이지네이션 응답"""
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = AdminAccountSerializer(many=True)


# ─── 어드민 회원 상세 조회 ────────────────────────────────────────────────────

class AdminCourseWithTagSerializer(serializers.ModelSerializer[Course]):
    class Meta:
        model = Course
        fields = ["id", "name", "tag"]
        read_only_fields = ["id", "name", "tag"]


class AdminCohortInfoSerializer(serializers.ModelSerializer[Cohort]):
    """admin_detail에서만 사용하는 Cohort 정보 (available_courses와 구분)"""
    class Meta:
        model = Cohort
        fields = ["id", "number", "start_date", "end_date", "status"]
        read_only_fields = fields


class AdminAssignedCourseSerializer(serializers.ModelSerializer[CohortStudents]):
    course = AdminCourseWithTagSerializer(source="cohort.course", read_only=True)
    cohort = AdminCohortInfoSerializer(read_only=True)

    class Meta:
        model = CohortStudents
        fields = ["course", "cohort"]


_DETAIL_FIELDS = [
    "id", "email", "nickname", "name", "phone_number", "birthday",
    "gender", "status", "role", "profile_img_url", "assigned_courses", "created_at",
]


class AdminAccountDetailSerializer(serializers.ModelSerializer[User]):
    status = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    assigned_courses = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = _DETAIL_FIELDS
        read_only_fields = _DETAIL_FIELDS

    @staticmethod
    def get_status(obj: User) -> str:
        if hasattr(obj, "withdrawal"):
            return "withdrew"
        return "active" if obj.is_active else "inactive"

    @staticmethod
    def get_role(obj: User) -> str:
        return obj.role.lower()

    @staticmethod
    def get_assigned_courses(obj: User) -> list[dict[str, Any]]:
        cohort_students = obj.cohort_students.all()
        ser = AdminAssignedCourseSerializer(cohort_students, many=True)
        return list(ser.data)


# ─── 어드민 권한 변경 ────────────────────────────────────────────────────────

class AdminPermissionSerializer(serializers.Serializer[Any]):
    ROLES = ["USER", "STUDENT", "ADMIN", "TA", "OM", "LC"]

    role = serializers.ChoiceField(choices=ROLES)
    cohort_id = serializers.IntegerField(required=False, allow_null=True)
    assigned_courses = serializers.ListField(child=serializers.IntegerField(), required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        role = attrs.get("role")
        cohort_id = attrs.get("cohort_id")
        assigned_courses = attrs.get("assigned_courses")

        if role in ["TA", "STUDENT"] and not cohort_id:
            raise serializers.ValidationError({"cohort_id": f"{role}로 권한 변경 시 필수 필드입니다."})

        if role in ["OM", "LC"] and not assigned_courses:
            raise serializers.ValidationError({"assigned_courses": f"{role}로 권한 변경 시 필수 필드입니다."})

        return attrs


# ─── 어드민 회원 정보 수정 ────────────────────────────────────────────────────

class AdminAccountUpdateSerializer(serializers.Serializer[Any]):
    nickname = serializers.CharField(max_length=10, required=False)
    name = serializers.CharField(max_length=30, required=False)
    phone_number = serializers.CharField(required=False)
    birthday = serializers.DateField(required=False)
    gender = serializers.ChoiceField(choices=User.Gender.choices, required=False)
    profile_img_url = serializers.URLField(required=False, allow_null=True, allow_blank=True)

    @staticmethod
    def validate_phone_number(value: str) -> str:
        if not re.fullmatch(r"\d{11}", value):
            raise serializers.ValidationError("11자리 숫자로 구성된 포맷이어야 합니다.")
        return value


class AdminAccountUpdateResponseSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = [
            "id", "email", "nickname", "name", "phone_number",
            "birthday", "gender", "profile_img_url", "updated_at",
        ]
        read_only_fields = fields
