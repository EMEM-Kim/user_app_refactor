"""
admin_urls.py
--------------
users 앱 어드민용 URL 통합 모음

통합 대상 (기존 파일):
  admin_urls.py → accounts (목록), accounts/<id> (상세/수정/삭제),
                  accounts/<id>/role (권한변경),
                  student-enrollments/accept (수강생 등록 승인)
  admin_student_enrollment_reject_view.py 의 URL도 여기로 통합
"""

from django.urls import path

from apps.users.views.admin_account_view import AdminAccountListView
from apps.users.views.admin_detail_view import AdminAccountView
from apps.users.views.admin_permission_view import AdminPermissionView
from apps.users.views.enrollment_accept_view import AdminStudentEnrollmentAcceptView
from apps.users.views.admin_student_enrollment_reject_view import AdminStudentEnrollmentRejectView

urlpatterns = [
    # ── 회원 관리 ──────────────────────────────────────────────────────────
    path("accounts", AdminAccountListView.as_view(), name="admin-account-list"),
    path("accounts/<int:account_id>", AdminAccountView.as_view(), name="admin-account-detail"),
    path("accounts/<int:account_id>/role", AdminPermissionView.as_view(), name="admin_permission"),

    # ── 수강생 등록 요청 처리 ──────────────────────────────────────────────
    path("student-enrollments/accept", AdminStudentEnrollmentAcceptView.as_view(), name="admin-student-enrollment-accept"),
    path("student-enrollments/reject", AdminStudentEnrollmentRejectView.as_view(), name="admin-student-enrollment-reject"),
]
