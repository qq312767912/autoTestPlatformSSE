from django.db import models
from django.contrib.auth.models import User


class ApiTestCaseTag(models.Model):
    """Test case tag."""
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=20, default="#1890ff")
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_testcase_tags',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_created_testcase_tags',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Test Case Tag"
        verbose_name_plural = "Test Case Tags"
        ordering = ['name']
        unique_together = ['name', 'project']

    def __str__(self):
        return self.name


class ApiTestCaseGroup(models.Model):
    """Hierarchical test case group."""
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_testcase_groups',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_created_testcase_groups',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Test Case Group"
        verbose_name_plural = "Test Case Groups"
        ordering = ['name']
        unique_together = ['name', 'parent', 'project']

    def __str__(self):
        return self.name

    def get_full_path(self):
        """Get the full hierarchical path."""
        path = [self.name]
        parent = self.parent
        while parent:
            path.append(parent.name)
            parent = parent.parent
        return ' / '.join(reversed(path))


class ApiTestCase(models.Model):
    """Test case model."""
    PRIORITY_CHOICES = [
        ('P0', 'P0 - Critical'),
        ('P1', 'P1 - High'),
        ('P2', 'P2 - Medium'),
        ('P3', 'P3 - Low'),
    ]

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=2, choices=PRIORITY_CHOICES, default='P2')
    config = models.JSONField(default=dict)
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_testcases',
    )
    group = models.ForeignKey(
        ApiTestCaseGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='api_testcases',
    )
    tags = models.ManyToManyField(
        ApiTestCaseTag,
        blank=True,
        related_name='api_testcases',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_created_testcases',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Test Case"
        verbose_name_plural = "Test Cases"
        ordering = ['-created_at']
        unique_together = ['name', 'project']

    def __str__(self):
        return self.name


class ApiTestCaseStep(models.Model):
    """Test case step model."""
    name = models.CharField(max_length=100)
    order = models.IntegerField()
    interface_data = models.JSONField()
    config = models.JSONField(default=dict, blank=True)
    testcase = models.ForeignKey(
        ApiTestCase,
        on_delete=models.CASCADE,
        related_name='steps',
    )
    origin_interface = models.ForeignKey(
        'api_interfaces.ApiInterface',
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_related_steps',
    )
    sync_fields = models.JSONField(default=list)
    last_sync_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Test Case Step"
        verbose_name_plural = "Test Case Steps"
        ordering = ['order']
        unique_together = ['testcase', 'order']

    def __str__(self):
        return f"{self.testcase.name}-{self.name}"


class ApiTestReport(models.Model):
    """Test report model."""
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failure', 'Failure'),
        ('error', 'Error'),
    ]

    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    success_count = models.IntegerField(default=0)
    fail_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    duration = models.FloatField()
    start_time = models.DateTimeField(auto_now_add=True)
    summary = models.JSONField()
    testcase = models.ForeignKey(
        ApiTestCase,
        on_delete=models.CASCADE,
        related_name='api_reports',
    )
    environment = models.ForeignKey(
        'api_environments.ApiEnvironment',
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_testcase_reports',
    )
    executed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_executed_testcase_reports',
    )

    class Meta:
        verbose_name = "Test Report"
        verbose_name_plural = "Test Reports"
        ordering = ['-start_time']

    def __str__(self):
        return self.name

    @property
    def project(self):
        """Return the project via the testcase FK for permission checks."""
        return self.testcase.project if self.testcase else None


class ApiTestReportDetail(models.Model):
    """Test report detail model."""
    report = models.ForeignKey(
        ApiTestReport,
        on_delete=models.CASCADE,
        related_name='details',
    )
    step = models.ForeignKey(
        ApiTestCaseStep,
        on_delete=models.SET_NULL,
        null=True,
        related_name='api_results',
    )
    success = models.BooleanField()
    elapsed = models.FloatField()
    request = models.JSONField()
    response = models.JSONField()
    validators = models.JSONField(default=list)
    extracted_variables = models.JSONField(default=dict)
    attachment = models.TextField(blank=True)

    class Meta:
        verbose_name = "Test Report Detail"
        verbose_name_plural = "Test Report Details"
        ordering = ['id']

    def __str__(self):
        report_name = self.report.name if self.report else "Unknown"
        step_name = self.step.name if self.step else "Unknown"
        return f"{report_name}-{step_name}"
