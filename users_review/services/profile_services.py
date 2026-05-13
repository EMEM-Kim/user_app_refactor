"""
profile_services.py
--------------------
프로필/계정 관리 관련 service 통합 모음

통합 대상 (기존 파일 → 클래스 / 함수):
  profile_image_service.py → update_profile_image
  password_change_service.py → PasswordChangeService
  change_phone_service.py  → change_phone_service
  withdrawal_service.py    → get_email_verify_token_cache_key, get_recovery_token_cache,
                             withdraw_user, restore_user, restore_user_by_token
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.users.models import User, Withdrawal
from apps.users.utils.purpose_enum import AuthPurpose, SmsPurpose
from apps.users.utils.user_exceptions import ConflictError
from apps.users.utils.withdrawal_exceptions import (
    AlreadyActiveError,
    AlreadyWithdrawnError,
    DeletedUserError,
    InvalidRecoveryTokenError,
    RecoveryPeriodExpiredError,
    WithdrawalRecordNotFoundError,
)

PHONE_CHANGE_PURPOSE = SmsPurpose.PHONE_CHANGE.value


# ─── 프로필 이미지 ───────────────────────────────────────────────────────────

def update_profile_image(user: User, profile_img_url: str | None) -> None:
    """프로필 이미지 URL을 저장합니다."""
    user.profile_img_url = profile_img_url
    user.save(update_fields=["profile_img_url"])


# ─── 비밀번호 변경 ───────────────────────────────────────────────────────────

class PasswordChangeService:
    @staticmethod
    def change_password(user: User, old_password: str, new_password: str) -> None:
        if not user.check_password(old_password):
            raise ValueError("현재 비밀번호가 일치하지 않습니다.")
        user.set_password(new_password)
        user.save(update_fields=["password"])


# ─── 휴대폰 번호 변경 ────────────────────────────────────────────────────────

def change_phone_service(validated_data: dict[str, Any], user: User) -> str:
    """
    SMS 인증 토큰을 받아 Redis 캐시에서 phone_number, purpose를 꺼낸 뒤
    용도를 확인하고 DB에 동일한 번호가 없으면 번호를 변경합니다.
    """
    sms_token = validated_data["phone_verify_token"]
    sms_key = f"sms_verify_token_{sms_token}"

    sms_data = cache.get(sms_key)

    if not sms_data:
        raise ValidationError("유효하지 않거나 만료된 인증 토큰입니다.")

    if PHONE_CHANGE_PURPOSE != sms_data.get("purpose"):
        raise ValidationError("유효하지 않거나 만료된 인증 토큰입니다.")

    new_phone_number: str = sms_data.get("phone_number")

    if user.phone_number == new_phone_number:
        raise ValidationError("현재 휴대폰 번호와 동일합니다.")

    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)

        if User.objects.filter(phone_number=new_phone_number).exists():
            raise ConflictError("이미 등록된 휴대폰 번호입니다.")

        locked_user.phone_number = new_phone_number
        locked_user.save(update_fields=["phone_number"])

    cache.delete(sms_key)
    return new_phone_number


# ─── 탈퇴 / 계정 복구 ────────────────────────────────────────────────────────

def get_email_verify_token_cache_key(email_token: str) -> str:
    """auth_email_service.py 의 verification_code() 가 저장하는 토큰 캐시 키 형식"""
    return f"email_verify_token_{email_token}"


def get_recovery_token_cache(email_token: str) -> dict[str, str]:
    """
    토큰 캐시 조회 + purpose=recovery 검증.
    다른 목적(signup / find_password)의 토큰이 계정 복구에 사용되지 않도록 구분합니다.
    """
    token_key = get_email_verify_token_cache_key(email_token)
    cached: Any = cache.get(token_key)

    if not isinstance(cached, dict) or cached.get("purpose") != AuthPurpose.RECOVERY.value:
        raise InvalidRecoveryTokenError()

    return cast(dict[str, str], cached)


def withdraw_user(user: User, reason: str, reason_detail: str = "") -> Withdrawal:
    """탈퇴 처리: Withdrawal 레코드 생성 + is_active=False"""
    try:
        with transaction.atomic():
            if Withdrawal.objects.filter(user=user).exists():
                raise AlreadyWithdrawnError()
            due_date = timezone.localdate() + timedelta(weeks=2)
            withdrawal = Withdrawal.objects.create(
                user=user,
                reason=reason,
                reason_detail=reason_detail,
                due_date=due_date,
            )
            user.is_active = False
            user.save(update_fields=["is_active", "updated_at"])
            return withdrawal
    except IntegrityError:
        raise AlreadyWithdrawnError()


def restore_user(user: User) -> None:
    """복구 처리: Withdrawal 레코드 삭제 + is_active=True"""
    with transaction.atomic():
        if user.is_active:
            raise AlreadyActiveError()
        withdrawal = Withdrawal.objects.filter(user=user).first()
        if withdrawal is None:
            raise WithdrawalRecordNotFoundError()
        if withdrawal.due_date <= timezone.localdate():
            raise RecoveryPeriodExpiredError()
        withdrawal.delete()
        user.is_active = True
        user.save(update_fields=["is_active", "updated_at"])


def restore_user_by_token(email_token: str) -> None:
    """
    이메일 인증 토큰으로 계정을 복구합니다.

    :param email_token: 이메일 인증 후 발급된 토큰 (Redis 저장, 10분 유효)
    :raises InvalidRecoveryTokenError: 토큰 없거나 purpose != recovery
    :raises DeletedUserError: 해당 이메일의 탈퇴 계정이 없는 경우
    """
    token_key = get_email_verify_token_cache_key(email_token)
    cached = get_recovery_token_cache(email_token)
    email: str = cached["email"]

    try:
        user = User.objects.get(email=email, is_active=False)
    except User.DoesNotExist:
        raise DeletedUserError()

    restore_user(user)
    cache.delete(token_key)
