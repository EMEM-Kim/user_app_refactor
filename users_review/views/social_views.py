"""
social_views.py
----------------
소셜 로그인 관련 view

통합 대상 (기존 파일 → 클래스):
  social_views.py → set_auth_cookies, SocialLoginView, SocialCallbackView
  (기존과 구조가 동일하므로 가독성 개선 + 서비스 import 경로만 social_services로 변경)
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.services.social_auth import SocialAuthService
from apps.users.utils.social_exceptions import SocialAuthError

logger = logging.getLogger(__name__)


def set_auth_cookies(response: Any, refresh: str) -> None:
    response.set_cookie(
        "refresh_token",
        refresh,
        max_age=60 * 60 * 24 * 4,  # 4일
        domain=getattr(settings, "COOKIE_DOMAIN", None),
        httponly=True,
        secure=True,
        samesite="None",
        path="/",
    )


class SocialLoginView(APIView):
    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        tags=["accounts"],
        summary="소셜 로그인 리다이렉트",
        description="지정된 provider의 OAuth 인증 URL로 리다이렉트합니다.",
        responses={302: None},
    )
    def get(self, request: HttpRequest, provider: str) -> HttpResponse:
        try:
            auth_url = SocialAuthService.get_auth_url(provider)
        except SocialAuthError as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return redirect(auth_url)


class SocialCallbackView(APIView):
    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        tags=["accounts"],
        summary="소셜 로그인 콜백 처리",
        description="OAuth 콜백 코드를 처리하고 프론트엔드로 리다이렉트합니다. 성공 시 refresh_token 쿠키를 설정합니다.",
        responses={302: None},
    )
    def get(self, request: HttpRequest, provider: str) -> HttpResponse:
        frontend_url: str = getattr(settings, "FRONTEND_REDIRECT_URI", "")

        try:
            result = SocialAuthService.process_user(
                provider=provider,
                code=request.GET.get("code", ""),
                state=request.GET.get("state", ""),
                error=request.GET.get("error"),
            )
        except SocialAuthError as e:
            logger.error(f"소셜 로그인 SocialAuthError: {e}")
            params = urlencode({"provider": provider, "is_success": "false"})
            return redirect(f"{frontend_url}/social-callback?{params}")
        except Exception as e:
            logger.error(f"소셜 로그인 에러: {e}", exc_info=True)
            params = urlencode({"provider": provider, "is_success": "false", "error": "server_error"})
            return redirect(f"{frontend_url}/social-callback?{params}")

        params = urlencode(
            {"provider": provider, "is_success": "true", "is_new_user": str(result["is_new_user"]).lower()}
        )
        response = redirect(f"{frontend_url}/social-callback?{params}")
        set_auth_cookies(response, result["refresh"])
        return response
