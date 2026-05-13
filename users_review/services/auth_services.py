"""
auth_services.py
-----------------
인증 관련 service 통합 모음

통합 대상 (기존 파일 → 클래스 / 함수):
  user_signup_service.py   → create_user
  user_login_service.py    → UserLoginService
  auth_email_service.py    → EmailVerificationService
  auth_sms_service.py      → SmsVerificationService
  find_email_service.py    → email_mask, find_email_service
  find_password_service.py → find_password_service
"""

from __future__ import annotations

import secrets
import time
import uuid
from typing import Any, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from twilio.base.exceptions import TwilioRestException  # type: ignore[import-untyped]
from twilio.rest import Client  # type: ignore[import-untyped]

from apps.core.utils.base62 import Base62
from apps.users.models import User
from apps.users.utils.purpose_enum import AuthPurpose, SmsPurpose
from apps.users.utils.user_exceptions import (
    ConflictError,
    InactiveError,
    InvalidLoginError,
    WithdrawnError,
)

_UserModel = get_user_model()

SIGNUP_PURPOSE_EMAIL = AuthPurpose.SIGNUP.value
SIGNUP_PURPOSE_SMS = SmsPurpose.SIGNUP.value


# ─── 회원가입 ────────────────────────────────────────────────────────────────

def create_user(validated_data: dict[str, Any]) -> User:
    """이메일/SMS 토큰 검증 후 유저를 생성합니다."""
    email_token = validated_data.pop("email_token")
    sms_token = validated_data.pop("sms_token")
    password = validated_data.pop("password")
    nickname = validated_data.get("nickname")

    email_key = f"email_verify_token_{email_token}"
    sms_key = f"sms_verify_token_{sms_token}"

    email_data = cache.get(email_key)
    sms_data = cache.get(sms_key)

    if not email_data:
        raise ValidationError("유효하지 않거나 만료된 이메일 인증입니다.")
    if not sms_data:
        raise ValidationError("유효하지 않거나 만료된 휴대폰 인증입니다.")

    if email_data.get("purpose") != SIGNUP_PURPOSE_EMAIL:
        raise ValidationError("유효하지 않거나 만료된 이메일 인증입니다.")
    if sms_data.get("purpose") != SIGNUP_PURPOSE_SMS:
        raise ValidationError("유효하지 않거나 만료된 휴대폰 인증입니다.")

    email = email_data.get("email")
    phone_number = sms_data.get("phone_number")

    if not email or not phone_number:
        raise ValidationError("인증 데이터에 문제가 있습니다. 다시 시도해 주세요.")

    try:
        with transaction.atomic():
            existing_users = User.objects.select_for_update().filter(
                Q(email=email) | Q(phone_number=phone_number) | Q(nickname=nickname)
            )
            for user in existing_users:
                if user.email == email:
                    raise ConflictError("이미 가입된 이메일입니다.")
                if user.phone_number == phone_number:
                    raise ConflictError("이미 가입에 사용된 휴대전화 번호입니다.")
                if user.nickname == nickname:
                    raise ConflictError("이미 사용 중인 닉네임입니다.")

            user = User.objects.create_user(
                email=email,
                password=password,
                phone_number=phone_number,
                **validated_data,
            )
            cache.delete(email_key)
            cache.delete(sms_key)
            return user

    except (ConflictError, ValidationError):
        raise


# ─── 로그인 / 토큰 ───────────────────────────────────────────────────────────

class UserLoginService:
    @staticmethod
    def verify_user(email: str, password: str) -> User:
        """이메일과 비밀번호를 검증하고 유저 객체를 반환."""
        user = User.objects.filter(email=email).first()

        if not user or not user.check_password(password):
            raise InvalidLoginError()

        if hasattr(user, "withdrawal") and user.withdrawal is not None:
            raise WithdrawnError(due_date=user.withdrawal.due_date)

        if not getattr(user, "is_active", False):
            raise InactiveError()

        return user

    @staticmethod
    def generate_token_pair(user: AbstractBaseUser) -> Tuple[str, str]:
        """유저 Access Token과 Refresh Token을 생성."""
        refresh = RefreshToken.for_user(user)
        return str(refresh.access_token), str(refresh)

    @staticmethod
    def add_to_blacklist(refresh_token_str: str) -> bool:
        """Redis 캐시에 토큰을 블랙리스트에 등록."""
        try:
            token = RefreshToken(refresh_token_str)  # type: ignore[arg-type]
            jti = token.payload.get("jti")
            exp = token.payload.get("exp")
            now = int(time.time())
            if not isinstance(exp, int) or not isinstance(jti, str):
                return True
            timeout = exp - now
            if timeout > 0:
                cache.set(f"blacklist_{jti}", "true", timeout)
            return True
        except TokenError:
            return True

    @staticmethod
    def is_blacklisted(refresh_token_str: str) -> bool:
        """토큰이 블랙리스트에 존재하는지 캐시를 확인."""
        token = RefreshToken(refresh_token_str)  # type: ignore[arg-type]
        jti = token.payload.get("jti")
        return cache.get(f"blacklist_{jti}") is not None


# ─── 이메일 인증 ─────────────────────────────────────────────────────────────

class EmailVerificationService:

    @classmethod
    def send_verification_email(cls, email: str, purpose: AuthPurpose) -> None:
        """
        Base62 코드 생성 후 Redis에 3분 저장한 뒤 이메일 발송.

        :param email: 유저 이메일
        :param purpose: [signup, find_password, recovery] 중 하나
        :raises ValidationError: 이메일 발송 실패 또는 캐시 저장 실패 시
        """
        if purpose == AuthPurpose.SIGNUP:
            if _UserModel.objects.filter(email=email).exists():
                raise ValidationError("이미 가입된 이메일입니다.")
        elif purpose == AuthPurpose.FIND_PASSWORD:
            if not _UserModel.objects.filter(email=email, is_active=True).exists():
                raise ValidationError("가입되지 않은 이메일 입니다.")
        elif purpose == AuthPurpose.RECOVERY:
            if not _UserModel.objects.filter(email=email, is_active=False).exists():
                raise ValidationError("복구 가능한 계정이 없습니다")

        code = Base62.uuid_encode(uuid.uuid4(), length=6)
        cache_key = f"email_code_{email}"
        cache_data = {"code": code, "purpose": purpose.value}  # type: ignore[misc]

        try:
            cache.set(cache_key, cache_data, timeout=180)
        except Exception as e:
            raise ValidationError(f"error: {e}  인증 코드 생성 중 서버 오류가 발생했습니다")

        subject = "[오즈코딩스쿨] 이메일 인증 코드를 확인해 주세요"
        message = f"인증 코드 : {code} 3분 이내에 입력해 주세요"

        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            cache.delete(cache_key)
            raise ValidationError("이메일 발송 실패 했습니다")

    @classmethod
    def verification_code(cls, email: str, code: str, purpose: AuthPurpose) -> str:
        """
        사용자가 입력한 코드를 Redis와 대조 후 일치 시 토큰 발급.

        :return: 인증 성공 시 email_token 문자열
        :raises ValidationError: 코드 불일치 또는 만료 시
        """
        cache_key = f"email_code_{email}"
        cached_data = cache.get(cache_key)

        if not cached_data:
            raise ValidationError("인증코드가 만료되거나 발급되지 않았습니다.")

        if cached_data.get("purpose") != purpose.value:  # type: ignore[misc]
            raise ValidationError("인증 용도가 일치하지 않습니다.")

        if cached_data.get("code") != code:
            raise ValidationError("인증코드가 만료되거나 일치하지 않습니다.")

        verify_token = secrets.token_urlsafe(32)
        token_key = f"email_verify_token_{verify_token}"
        data = {"email": email, "purpose": purpose.value}  # type: ignore[misc]

        try:
            cache.set(token_key, data, timeout=600)
        except Exception:
            raise ValidationError("토큰 발급 중 서버 오류가 발생했습니다.")

        cache.delete(cache_key)
        return verify_token


# ─── SMS 인증 ────────────────────────────────────────────────────────────────

class SmsVerificationService:
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    service_sid = settings.TWILIO_VERIFY_SERVICE_SID

    @classmethod
    def phone_format_change(cls, phone_number: str) -> str:
        """01012345678 → +821012345678"""
        if phone_number.startswith("+82"):
            return phone_number
        return f"+82{phone_number.lstrip('0')}"

    @classmethod
    def send_verification_sms(cls, phone_number: str, purpose: SmsPurpose) -> None:
        if purpose == SmsPurpose.SIGNUP:
            if _UserModel.objects.filter(phone_number=phone_number).exists():
                raise ValidationError("이미 등록된 전화번호 입니다")
        elif purpose == SmsPurpose.FIND_EMAIL:
            if not _UserModel.objects.filter(phone_number=phone_number).exists():
                raise ValidationError("등록된 전화번호가 아닙니다.")
        elif purpose == SmsPurpose.PHONE_CHANGE:
            if _UserModel.objects.filter(phone_number=phone_number).exists():
                raise ValidationError("변경가능한 번호가 아닙니다")

        cache_key = f"sms_code_{phone_number}"
        cache_data = {"purpose": purpose.value}  # type: ignore[misc]

        try:
            cache.set(cache_key, cache_data, timeout=180)
        except Exception as e:
            cache.delete(cache_key)
            raise ValidationError(f"error: {e}  서버 오류가 발생했습니다")

        formatted_phone = cls.phone_format_change(phone_number)

        try:
            cls.client.verify.v2.services(cls.service_sid).verifications.create(
                to=formatted_phone, channel="sms"
            )
        except TwilioRestException as e:
            cache.delete(cache_key)
            raise ValidationError(f"SMS 발송 실패: {e.msg}")

    @classmethod
    def verify_sms_code(cls, phone_number: str, code: str, purpose: SmsPurpose) -> str:
        """
        사용자가 입력한 코드를 Twilio에 보내서 확인하고,
        성공 시 다음 단계용 sms_token을 발급합니다.
        """
        formatted_phone = cls.phone_format_change(phone_number)
        cache_key = f"sms_code_{phone_number}"
        cached_data = cache.get(cache_key)

        if not cached_data:
            raise ValidationError("인증 코드가 만료되었거나 발급되지 않았습니다.")

        if cached_data.get("purpose") != purpose.value:  # type: ignore[misc]
            raise ValidationError("인증 용도가 일치하지 않습니다.")

        try:
            verification_check = cls.client.verify.v2.services(cls.service_sid).verification_checks.create(
                to=formatted_phone, code=code
            )

            if verification_check.status == "approved":
                sms_token = secrets.token_urlsafe(32)
                token_key = f"sms_verify_token_{sms_token}"
                data = {"phone_number": phone_number, "purpose": purpose.value}  # type: ignore[misc]

                try:
                    cache.set(token_key, data, timeout=600)
                except Exception as e:
                    raise ValidationError(f"error: {e} 서버에 오류가 발생했습니다")

                cache.delete(cache_key)
                return sms_token

            raise ValidationError("인증 코드가 일치하지 않거나 만료되었습니다.")

        except TwilioRestException as e:
            raise ValidationError(f"인증 확인 중 오류 발생: {e.msg}")


# ─── 이메일 찾기 ─────────────────────────────────────────────────────────────

def email_mask(email: str) -> str:
    """이메일 일부를 '*'로 마스킹하여 반환합니다."""
    username, domain = email.rsplit("@", 1)
    domain_name, domain_ext = domain.split(".", 1)
    username_masked = username[0] + "*" * (len(username) - 2) + username[-1]
    domain_masked = domain_name[0] + "*" * (len(domain_name) - 3) + domain_name[-2:]
    return f"{username_masked}@{domain_masked}.{domain_ext}"


def find_email_service(validated_data: dict[str, Any]) -> str:
    """SMS 인증 토큰으로 이름+전화번호를 조회하여 마스킹된 이메일을 반환합니다."""
    sms_token = validated_data["sms_token"]
    name = validated_data["name"]

    sms_key = f"sms_verify_token_{sms_token}"
    sms_data = cache.get(sms_key)

    if not sms_data:
        raise ValidationError("유효하지 않거나 만료된 인증 토큰입니다.")

    purpose = SmsPurpose(sms_data.get("purpose"))
    if purpose != SmsPurpose.FIND_EMAIL:
        raise ValidationError("유효하지 않거나 만료된 인증 토큰입니다.")

    phone_number = sms_data.get("phone_number")

    try:
        user = User.objects.get(name=name, phone_number=phone_number)
        email = user.email
        cache.delete(sms_key)
        return email_mask(email)
    except User.DoesNotExist:
        raise ValidationError("해당 정보를 가진 사용자를 찾을 수 없습니다.")


# ─── 비밀번호 찾기 ───────────────────────────────────────────────────────────

FIND_PASSWORD_PURPOSE = AuthPurpose.FIND_PASSWORD.value


def find_password_service(validated_data: dict[str, Any]) -> None:
    """이메일 인증 토큰으로 유저를 조회하여 새 비밀번호로 변경합니다."""
    email_token = validated_data["email_token"]
    new_password = validated_data["new_password"]

    email_key = f"email_verify_token_{email_token}"
    email_data = cache.get(email_key)

    if not email_data:
        raise ValidationError("유효하지 않거나 만료된 인증 토큰입니다.")

    if FIND_PASSWORD_PURPOSE != email_data.get("purpose"):
        raise ValidationError("유효하지 않거나 만료된 인증 토큰입니다.")

    email = email_data.get("email")

    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(email=email)
            if user.check_password(new_password):
                raise ValidationError("기존 비밀번호와 동일한 비밀번호로 변경할 수 없습니다.")
            user.set_password(new_password)
            user.save()

        cache.delete(email_key)

    except User.DoesNotExist:
        raise ValidationError("해당 이메일을 가진 사용자를 찾을 수 없습니다.")
