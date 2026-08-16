from django.urls import path

from .views import (
    ApproveView, CalculateView, CloseView, EmployeePayslipsView,
    EmployeeProjectionView, EntryManualLinesView, EntryPayslipView, EntryView,
    PeriodPayslipsView, PeriodView, PeriodsView, ReopenView,
)

app_name = "payroll"

urlpatterns = [
    path("payroll/periods/", PeriodsView.as_view(), name="periods"),
    path("payroll/periods/<uuid:pk>/", PeriodView.as_view(), name="period"),
    path("payroll/periods/<uuid:pk>/calculate/", CalculateView.as_view(), name="calculate"),
    path("payroll/periods/<uuid:pk>/approve/", ApproveView.as_view(), name="approve"),
    path("payroll/periods/<uuid:pk>/reopen/", ReopenView.as_view(), name="reopen"),
    path("payroll/periods/<uuid:pk>/close/", CloseView.as_view(), name="close"),
    path("payroll/periods/<uuid:pk>/payslips/", PeriodPayslipsView.as_view(), name="period-payslips"),
    path("payroll/entries/<uuid:pk>/", EntryView.as_view(), name="entry"),
    path("payroll/entries/<uuid:pk>/manual-lines/", EntryManualLinesView.as_view(), name="entry-manual-lines"),
    path("payroll/entries/<uuid:pk>/payslip/", EntryPayslipView.as_view(), name="entry-payslip"),
    path(
        "payroll/employees/<uuid:pk>/payslips/",
        EmployeePayslipsView.as_view(),
        name="employee-payslips",
    ),
    path(
        "payroll/employees/<uuid:pk>/tax-projection/<int:year>/",
        EmployeeProjectionView.as_view(),
        name="employee-tax-projection",
    ),
]
