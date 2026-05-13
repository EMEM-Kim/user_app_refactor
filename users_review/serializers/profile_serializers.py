"""
profile_serializers.py
-----------------------
프로필/계정 관리 관련 serializer 통합 모음

통합 대상 (기존 파일 → 클래스):
  user_info_serializer.py     → UserInfoSerializer, UserInfoUpdateSerializer, UserInfoUpdateResponseSerializer
  check_nickname_serializer.py → CheckNicknameSerializer
  profile_image_serializer.py → ProfileImageUpdateSerializer
  password_change_serializer.py → PasswordChangeSerializer
  change_phone_serializer.py  → ChangePhoneSerializer
  withdrawal_serializer.py    → WithdrawalSerializer, RestoreSerializer
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, cast

from rest_framework import serializers

from apps.users.models import User, Withdrawal
from apps.users.services.withdrawal_service import get_recovery_token_cache
from apps.users.utils.user_exceptions import DuplicateNicknameError
from apps.users.utils.withdrawal_exceptions import InvalidRecoveryTokenError


# ─── 내 정보 조회 / 수정 ─────────────────────────────────────────────────────

class UserInfoSerializer(serializers.ModelSerializer[User]):
    """내 정보 조회"""
    cohort_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "nickname", "name", "phone_number",
            "birthday", "gender", "profile_img_url", "cohort_id", "role", "created_at",
        ]
        read_only_fields = fields

    def get_cohort_id(self, obj: User) -> int | None:
        cohort_student = obj.cohort_students.first()
        if cohort_student is None or cohort_student.cohort is None:
            return None
        return cohort_student.cohort.id


class UserInfoUpdateSerializer(serializers.ModelSerializer[User]):
    """내 정보 수정"""

    class Meta:
        model = User
        fields = ["nickname", "name", "birthday", "gender"]

    def validate_nickname(self, value: str) -> str:
        if not re.match(r"^[가-힣a-zA-Z0-9]{2,10}$", value):
            raise serializers.ValidationError("닉네임은 2~10자 이내, 특수문자 제외, 한글/영문/숫자만 허용됩니다.")
        instance = cast(User, self.instance)
        if User.objects.filter(nickname=value).exclude(id=instance.id).exists():
            raise DuplicateNicknameError()
        return value

    def validate_name(self, value: str) -> str:
        if not re.match(r"^[가-힣a-zA-Z\s]{1,30}$", value):
            raise serializers.ValidationError("이름은1~30자 이내, 특수문자 제외, 한글/영문만 허용됩니다.")
        return value

    def validate_birthday(self, value: date) -> date:
        if value > date.today():
            raise serializers.ValidationError("생일은 미래 날짜로 등록할 수 없습니다.")
        return value


class UserInfoUpdateResponseSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ["id", "email", "nickname", "name", "birthday", "gender", "phone_number", "updated_at"]
        read_only_fields = fields


# ─── 닉네임 중복 확인 ────────────────────────────────────────────────────────

class CheckNicknameSerializer(serializers.Serializer[Any]):
    nickname = serializers.CharField()

    def validate_nickname(self, value: str) -> str:
        clean_value = value.strip()
        if not re.match(r"^[가-힣a-zA-Z0-9]{2,10}$", clean_value):
            raise serializers.ValidationError(
                "닉네임은 2~10자 이내, 특수문자 제외, 공백제외, 한글/영문/숫자만 허용됩니다."
            )
        if User.objects.filter(nickname=clean_value).exists():
            raise DuplicateNicknameError()
        return clean_value


# ─── 프로필 이미지 ───────────────────────────────────────────────────────────

class ProfileImageUpdateSerializer(serializers.Serializer[Any]):
    profile_img_url = serializers.CharField(
        allow_null=True,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )


# ─── 비밀번호 변경 ───────────────────────────────────────────────────────────

class PasswordChangeSerializer(serializers.Serializer[Any]):
    old_password = serializers.CharField(
        write_only=True, required=True,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )
    new_password = serializers.CharField(
        write_only=True, required=True,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )

    def validate_new_password(self, value: str) -> str:
        if not re.match(r"^\S{6,15}$", value):
            raise serializers.ValidationError("비밀번호는 6~15자여야 합니다.")
        if not re.search(r"[a-zA-Z]", value):
            raise serializers.ValidationError("비밀번호는 영문을 포함해야 합니다.")
        if not re.search(r"[0-9]", value):
            raise serializers.ValidationError("비밀번호는 숫자를 포함해야 합니다.")
        if not re.search(r"[!@#$%^&*]", value):
            raise serializers.ValidationError("비밀번호는 특수문자를 포함해야 합니다.")
        return value


# ─── 휴대폰 번호 변경 ────────────────────────────────────────────────────────

class ChangePhoneSerializer(serializers.Serializer[Any]):
    phone_verify_token = serializers.CharField(
        required=True,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )


# ─── 탈퇴 / 계정 복구 ────────────────────────────────────────────────────────

class WithdrawalSerializer(serializers.Serializer[Any]):
    reason = serializers.ChoiceField(choices=Withdrawal.Reason.choices)
    reason_detail = serializers.CharField(required=False, default="", allow_blank=True)


class RestoreSerializer(serializers.Serializer[Any]):
    email_token = serializers.CharField()

    def validate_email_token(self, value: str) -> str:
        # View가 아닌 Serializer에서 토큰 검증 (피드백 반영)
        # Redis 캐시에서 purpose=recovery 여부 확인
        # → signup/find_password 등 다른 purpose 토큰이 복구에 사용되지 않도록 구분
        try:
            get_recovery_token_cache(value)
        except InvalidRecoveryTokenError:
            raise serializers.ValidationError("유효하지 않은 복구 토큰입니다.")
        return value
