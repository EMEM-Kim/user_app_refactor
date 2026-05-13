"""
social_services.py
-------------------
소셜 로그인 관련 service 통합 모음

통합 대상 (기존 파일 → 클래스):
  kakao.py       → KakaoUserInfo, KakaoOAuthService
  naver.py       → NaverUserInfo, NaverOAuthService
  social_auth.py → SocialAuthService
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Union

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import SocialUsers, User
from apps.users.services.user_login_service import UserLoginService
from apps.users.utils.social_exceptions import (
    EmailAlreadyRegisteredError,
    EmailNotProvidedError,
    MissingAuthCodeError,
    OAuthCallbackError,
    UnsupportedProviderError,
)


# ─── 카카오 ──────────────────────────────────────────────────────────────────

@dataclass
class KakaoUserInfo:
    provider_id: str
    nickname: Optional[str]
    profile_img_url: Optional[str]
    email: Optional[str]
    name: Optional[str]
    phone_number: Optional[str]
    gender: Optional[str]
    birthday: Optional[str]  # YYYY-MM-DD


class KakaoOAuthService:
    AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
    TOKEN_URL = "https://kauth.kakao.com/oauth/token"
    USER_INFO_URL = "https://kapi.kakao.com/v2/user/me"

    _GENDER_MAP = {"male": "M", "female": "F"}

    @classmethod
    def get_auth_url(cls, state: str = "") -> str:
        return (
            f"{cls.AUTH_URL}"
            f"?client_id={settings.KAKAO_CLIENT_ID}"
            f"&redirect_uri={settings.KAKAO_REDIRECT_URI}"
            "&response_type=code"
            f"&state={state}"
        )

    @classmethod
    def get_access_token(cls, code: str, redirect_uri: str) -> str:
        response = requests.post(
            cls.TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.KAKAO_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise ValueError(f"카카오 토큰 발급 오류: {data.get('error_description', data['error'])}")

        access_token = data.get("access_token")
        if not access_token:
            raise ValueError("카카오 응답에 access_token이 없습니다.")

        return str(access_token)

    @classmethod
    def get_user_info(cls, access_token: str) -> KakaoUserInfo:
        response = requests.get(
            cls.USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        account = data.get("kakao_account", {})
        profile = account.get("profile", {})

        birthyear = account.get("birthyear")
        birthday_mmdd = account.get("birthday")
        if birthyear and birthday_mmdd and len(birthday_mmdd) == 4:
            birthday = f"{birthyear}-{birthday_mmdd[:2]}-{birthday_mmdd[2:]}"
        else:
            birthday = None

        raw_gender = account.get("gender")
        gender = cls._GENDER_MAP.get(raw_gender) if raw_gender else None

        raw_phone = account.get("phone_number", "")
        phone_number = cls._normalize_phone(raw_phone) if raw_phone else None

        return KakaoUserInfo(
            provider_id=str(data["id"]),
            nickname=profile.get("nickname"),
            profile_img_url=profile.get("profile_image_url"),
            email=account.get("email"),
            name=account.get("name"),
            phone_number=phone_number,
            gender=gender,
            birthday=birthday,
        )

    @classmethod
    def get_user_info_by_code(cls, code: str, redirect_uri: str) -> KakaoUserInfo:
        access_token = cls.get_access_token(code, redirect_uri)
        return cls.get_user_info(access_token)

    @staticmethod
    def _normalize_phone(raw: str) -> str:
        phone = raw.rstrip()
        if phone.startswith("+82"):
            phone = "0" + phone[3:].strip()
        return phone.replace("-", "").replace(" ", "")


# ─── 네이버 ──────────────────────────────────────────────────────────────────

@dataclass
class NaverUserInfo:
    provider_id: str
    email: Optional[str]
    name: Optional[str]
    nickname: Optional[str]
    profile_img_url: Optional[str]
    phone_number: Optional[str]
    gender: Optional[str]
    birthday: Optional[str]  # YYYY-MM-DD


class NaverOAuthService:
    AUTH_URL = "https://nid.naver.com/oauth2.0/authorize"
    TOKEN_URL = "https://nid.naver.com/oauth2.0/token"
    USER_INFO_URL = "https://openapi.naver.com/v1/nid/me"

    @classmethod
    def get_auth_url(cls, state: str | None = None) -> str:
        return (
            f"{cls.AUTH_URL}"
            f"?client_id={settings.NAVER_CLIENT_ID}"
            f"&redirect_uri={settings.NAVER_REDIRECT_URI}"
            "&response_type=code"
            f"&state={state}"
        )

    @classmethod
    def get_access_token(cls, code: str, state: str) -> str:
        response = requests.post(
            cls.TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.NAVER_CLIENT_ID,
                "client_secret": settings.NAVER_CLIENT_SECRET,
                "redirect_uri": settings.NAVER_REDIRECT_URI,
                "code": code,
                "state": state,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise ValueError(f"네이버 토큰 발급 오류: {data.get('error_description', data['error'])}")

        access_token = data.get("access_token")
        if not access_token:
            raise ValueError("네이버 응답에 access_token이 없습니다.")

        return str(access_token)

    @classmethod
    def get_user_info(cls, access_token: str) -> NaverUserInfo:
        response = requests.get(
            cls.USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        user_data = data.get("response", {})

        raw_phone = user_data.get("mobile", "")
        phone_number = raw_phone.replace("-", "") if raw_phone else None

        gender = user_data.get("gender") or None

        birthyear = user_data.get("birthyear", "")
        birthday_mmdd = user_data.get("birthday", "")
        birthday = f"{birthyear}-{birthday_mmdd}" if birthyear and birthday_mmdd else None

        return NaverUserInfo(
            provider_id=str(user_data["id"]),
            email=user_data.get("email"),
            name=user_data.get("name"),
            nickname=user_data.get("nickname"),
            profile_img_url=user_data.get("profile_image"),
            phone_number=phone_number,
            gender=gender,
            birthday=birthday,
        )

    @classmethod
    def get_user_info_by_code(cls, code: str, state: str) -> NaverUserInfo:
        access_token = cls.get_access_token(code, state)
        return cls.get_user_info(access_token)


# ─── 소셜 인증 통합 ──────────────────────────────────────────────────────────

_UserInfo = Union[KakaoUserInfo, NaverUserInfo]

_OAUTH_SERVICES: dict[str, Any] = {
    "kakao": KakaoOAuthService,
    "naver": NaverOAuthService,
}


class SocialAuthService:
    STATE_TTL = 300

    @classmethod
    def get_auth_url(cls, provider: str) -> str:
        service = _OAUTH_SERVICES.get(provider)
        if service is None:
            raise UnsupportedProviderError()

        if provider == "naver":
            state = secrets.token_urlsafe(16)
            cache.set(f"oauth_state:{state}", provider, timeout=cls.STATE_TTL)
            return str(service.get_auth_url(state))

        return str(service.get_auth_url())

    @classmethod
    def process_user(
        cls,
        provider: str,
        code: str,
        state: str = "",
        error: str | None = None,
    ) -> dict[str, Any]:
        if error:
            raise OAuthCallbackError(error)
        if not code:
            raise MissingAuthCodeError()
        if provider == "naver":
            if not state or not cache.get(f"oauth_state:{state}"):
                raise OAuthCallbackError("유효하지 않은 state입니다.")
            cache.delete(f"oauth_state:{state}")
        user_info = cls._get_user_info(provider, code, state)
        return cls._login_and_register(provider, user_info)

    @classmethod
    def _get_user_info(cls, provider: str, code: str, state: str = "") -> _UserInfo:
        if provider == "kakao":
            redirect_uri: str = getattr(settings, "KAKAO_REDIRECT_URI", "")
            return KakaoOAuthService.get_user_info_by_code(code, redirect_uri)

        if provider == "naver":
            return NaverOAuthService.get_user_info_by_code(code, state)

        raise UnsupportedProviderError()

    @classmethod
    def _login_and_register(cls, provider: str, user_info: _UserInfo) -> dict[str, Any]:
        try:
            social_user = SocialUsers.objects.select_related("user").get(
                provider=provider,
                provider_id=user_info.provider_id,
            )
            return cls._generate_token_result(social_user.user, is_new_user=False)
        except SocialUsers.DoesNotExist:
            pass

        if not user_info.email:
            raise EmailNotProvidedError()

        if User.objects.filter(email=user_info.email).exists():
            raise EmailAlreadyRegisteredError()

        with transaction.atomic():
            user = cls._create_social_user(user_info)
            SocialUsers.objects.create(
                user=user,
                provider=provider,
                provider_id=user_info.provider_id,
            )
        return cls._generate_token_result(user, is_new_user=True)

    @classmethod
    def _create_social_user(cls, user_info: _UserInfo) -> User:
        user = User(
            email=user_info.email or "",
            name=user_info.name or "",
            nickname=user_info.nickname or "",
            phone_number=user_info.phone_number or f"social_{uuid.uuid4().hex[:12]}",
            profile_img_url=user_info.profile_img_url,
            gender=user_info.gender,
            birthday=user_info.birthday,
        )
        user.set_unusable_password()
        user.save()
        return user

    @classmethod
    def _generate_token_result(cls, user: User, is_new_user: bool) -> dict[str, Any]:
        access, refresh = UserLoginService.generate_token_pair(user)
        return {
            "is_new_user": is_new_user,
            "access": access,
            "refresh": refresh,
        }
