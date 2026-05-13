"""
auth_serializers.py
--------------------
인증/계정 관련 serializer 통합 모음

통합 대상 (기존 파일 → 클래스):
  user_signup_serializer.py   → SignupSerializer
  user_login_serializer.py    → LoginSerializer, TokenRefreshSerializer
  auth_email_serializer.py    → EmailRequestSerializer, EmailVerifySerializer
  auth_sms_serializer.py      → SmsSendSerializer, SmsVerifySerializer
  find_email_serializer.py    → FindEmailSerializer, FindEmailResponseSerializer
  find_password_serializer.py → FindPasswordSerializer
  social_serializers.py       → SocialRegisterSerializer
"""

from __future__ import annotations

import re
from typing import Any

from rest_framework import serializers

from apps.users.models import User
from apps.users.services.user_signup_service import create_user
from apps.users.utils.purpose_enum import AuthPurpose, SmsPurpose


# ─── 회원가입 ────────────────────────────────────────────────────────────────

class SignupSerializer(serializers.ModelSerializer["User"]):
    email_token = serializers.CharField(write_only=True)
    sms_token = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)
    gender = serializers.ChoiceField(
        choices=User.Gender.choices,
        error_messages={"invalid_choice": "유효하지 않은 성별입니다. M(남성) 또는 F(여성)만 허용됩니다."},
    )

    class Meta:
        model = User
        fields = ["password", "nickname", "name", "birthday", "gender", "email_token", "sms_token"]
        extra_kwargs: dict[str, dict[str, Any]] = {
            "nickname": {"validators": []},
        }

    def validate_nickname(self, value: str) -> str:
        if not re.match(r"^[가-힣a-zA-Z0-9]{2,10}$", value):
            raise serializers.ValidationError("닉네임은 2~10자 이내, 특수문자 제외, 한글/영문/숫자만 허용됩니다.")
        return value

    def validate_password(self, value: str) -> str:
        if not re.match(r"^\S{6,15}$", value):
            raise serializers.ValidationError("비밀번호는 6~15자여야 합니다.")
        if not re.search(r"[a-zA-Z]", value):
            raise serializers.ValidationError("비밀번호는 영문을 포함해야 합니다.")
        if not re.search(r"[0-9]", value):
            raise serializers.ValidationError("비밀번호는 숫자를 포함해야 합니다.")
        if not re.search(r"[!@#$%^&*]", value):
            raise serializers.ValidationError("비밀번호는 특수문자를 포함해야 합니다.")
        return value

    def create(self, validated_data: dict[str, str]) -> User:
        return create_user(validated_data)


# ─── 로그인 / 토큰 ───────────────────────────────────────────────────────────

class LoginSerializer(serializers.Serializer[Any]):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class TokenRefreshSerializer(serializers.Serializer[Any]):
    refresh_token = serializers.CharField(
        required=True,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )


# ─── 이메일 인증 ─────────────────────────────────────────────────────────────

class EmailRequestSerializer(serializers.Serializer[Any]):
    """인증 코드 발송 요청"""
    email = serializers.EmailField(error_messages={"required": "이 필드는 필수 항목입니다."})
    purpose = serializers.ChoiceField(
        choices=AuthPurpose.choices,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )


class EmailVerifySerializer(EmailRequestSerializer):
    """인증 코드 확인"""
    code = serializers.CharField(min_length=6, max_length=6, error_messages={"required": "이 필드는 필수 항목입니다."})

    def validate_code(self, value: str) -> str:
        if not value.isalnum():
            raise serializers.ValidationError("인증 코드는 영문자와 숫자로만 이루어져야 합니다.")
        return value


# ─── SMS 인증 ────────────────────────────────────────────────────────────────

class SmsSendSerializer(serializers.Serializer[Any]):
    phone_number = serializers.CharField(
        max_length=20,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )
    purpose = serializers.ChoiceField(
        choices=SmsPurpose.choices,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )

    def validate_phone_number(self, value: str) -> str:
        clean_value = value.replace("-", "")
        if not clean_value.isdigit():
            raise serializers.ValidationError("전화번호는 숫자여야 합니다.")
        return clean_value


class SmsVerifySerializer(SmsSendSerializer):
    code = serializers.CharField(
        max_length=6,
        min_length=6,
        error_messages={"required": "이 필드는 필수 항목입니다."},
    )

    def validate_code(self, value: str) -> str:
        if not value.isdigit():
            raise serializers.ValidationError("인증코드는 숫자만 입력되어야 합니다")
        return value


# ─── 이메일 / 비밀번호 찾기 ──────────────────────────────────────────────────

class FindEmailSerializer(serializers.Serializer[Any]):
    sms_token = serializers.CharField(required=True, error_messages={"required": "이 필드는 필수 항목입니다."})
    name = serializers.CharField(required=True, error_messages={"required": "이 필드는 필수 항목입니다."})


class FindEmailResponseSerializer(serializers.Serializer[Any]):
    email = serializers.CharField()


class FindPasswordSerializer(serializers.Serializer[Any]):
    email_token = serializers.CharField(required=True, error_messages={"required": "이 필드는 필수 항목입니다."})
    new_password = serializers.CharField(
        write_only=True, required=True, error_messages={"required": "이 필드는 필수 항목입니다."}
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


# ─── 소셜 회원가입 ───────────────────────────────────────────────────────────

class SocialRegisterSerializer(serializers.ModelSerializer[User]):
    social_token = serializers.CharField(allow_blank=False)

    class Meta:
        model = User
        fields = ["social_token", "email", "name", "nickname", "phone_number", "birthday", "gender"]
