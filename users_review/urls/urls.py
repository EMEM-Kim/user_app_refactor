"""
urls.py
--------
users 앱 일반 사용자용 URL 통합 모음

통합 대상 (기존 파일):
  auth_email_url.py    → verification/send-email, verification/verify-email
  auth_sms_url.py      → verification/send-sms, verification/verify-sms
  user_signup_url.py   → signup, enroll-student, me, available-courses, me/enrolled-courses, check-nickname
  user_login_url.py    → login, logout, me/refresh
  find_email_url.py    → find-email
  find_password_url.py → find_password
  password_change_url.py → change-password
  change_phone_url.py  → change-phone
  profile_image_urls.py → me/profile-image/presigned-url, me/profile-image
  social_url.py        → social-login/<provider>, social-login/<provider>/callback
  withdrawal_urls.py   → withdrawal, restore

주의: withdrawal 경로는 me(UserInfoView) 보다 먼저 include되어야 합니다.
     Django URL 매칭 순서에 따라 WithdrawalView가 'me' 경로보다 먼저 등록됩니다.
"""

from django.urls import path
from rest_framework.permissions import IsAuthenticated

from apps.core.presigned_url.views import PresignedUrlView
from apps.users.views.auth_email_view import EmailSendView, EmailVerificationView
from apps.users.views.auth_sms_view import SmsSendView, SmsVerificationView
from apps.users.views.change_phone_view import ChangePhoneView
from apps.users.views.check_nickname_view import CheckNicknameView
from apps.users.views.enrolled_courses_view import MyCoursesView
from apps.users.views.enrollment_view import EnrollmentView
from apps.users.views.find_email_view import FindEmailView
from apps.users.views.find_password_view import FindPasswordView
from apps.users.views.available_courses_view import AvailableCoursesView
from apps.users.views.password_change_view import PasswordChangeView
from apps.users.views.profile_image_views import ProfileImageView
from apps.users.views.social_views import SocialCallbackView, SocialLoginView
from apps.users.views.user_info_view import UserInfoView
from apps.users.views.user_login_view import LoginView, LogoutView, TokenRefreshView
from apps.users.views.user_signup_view import SignupView
from apps.users.views.withdrawal_view import RestoreView, WithdrawalView


class ProfileImageUploadView(PresignedUrlView):
    path = "uploads/images/profiles"
    permission_classes = [IsAuthenticated]


app_name = "users"

urlpatterns = [
    # ── 이메일 인증 ────────────────────────────────────────────────────────
    path("verification/send-email", EmailSendView.as_view(), name="send-email"),
    path("verification/verify-email", EmailVerificationView.as_view(), name="verify-email"),

    # ── SMS 인증 ───────────────────────────────────────────────────────────
    path("verification/send-sms", SmsSendView.as_view(), name="send-sms"),
    path("verification/verify-sms", SmsVerificationView.as_view(), name="verify-sms"),

    # ── 소셜 로그인 ────────────────────────────────────────────────────────
    path("social-login/<str:provider>", SocialLoginView.as_view(), name="social-login"),
    path("social-login/<str:provider>/callback", SocialCallbackView.as_view(), name="social-callback"),

    # ── 회원가입 / 탈퇴 / 계정 복구 ───────────────────────────────────────
    # ※ withdrawal 은 UserInfoView(me) 보다 먼저 등록되어야 DELETE /me 가 WithdrawalView로 라우팅됩니다.
    path("withdrawal", WithdrawalView.as_view(), name="withdrawal"),
    path("restore", RestoreView.as_view(), name="restore"),
    path("signup", SignupView.as_view(), name="signup"),

    # ── 로그인 / 로그아웃 / 토큰 재발급 ───────────────────────────────────
    path("login", LoginView.as_view(), name="login"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("me/refresh", TokenRefreshView.as_view(), name="token_refresh"),

    # ── 내 정보 / 프로필 ───────────────────────────────────────────────────
    path("me", UserInfoView.as_view(), name="me"),
    path("me/profile-image/presigned-url", ProfileImageUploadView.as_view(), name="profile-image-presigned-url"),
    path("me/profile-image", ProfileImageView.as_view(), name="profile-image"),
    path("change-password", PasswordChangeView.as_view(), name="change-password"),
    path("change-phone", ChangePhoneView.as_view(), name="change-phone"),
    path("check-nickname", CheckNicknameView.as_view(), name="check-nickname"),

    # ── 이메일 / 비밀번호 찾기 ─────────────────────────────────────────────
    path("find-email", FindEmailView.as_view(), name="find_email"),
    path("find_password", FindPasswordView.as_view(), name="find_password"),

    # ── 수강신청 ───────────────────────────────────────────────────────────
    path("enroll-student", EnrollmentView.as_view(), name="enroll-student"),
    path("available-courses", AvailableCoursesView.as_view(), name="available-courses"),
    path("me/enrolled-courses", MyCoursesView.as_view(), name="enrolled-courses"),
]
