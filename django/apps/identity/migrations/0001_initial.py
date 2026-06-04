"""Initial migration for identity app."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("password_hash", models.CharField(max_length=256)),
                ("display_name", models.CharField(max_length=100)),
                ("first_name", models.CharField(blank=True, max_length=100)),
                ("last_name", models.CharField(blank=True, max_length=100)),
                ("avatar_url", models.URLField(blank=True, null=True)),
                ("bio", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("is_creator", models.BooleanField(default=False)),
                ("is_seller", models.BooleanField(default=False)),
                ("is_admin", models.BooleanField(default=False)),
                ("follower_count", models.PositiveIntegerField(default=0)),
                ("following_count", models.PositiveIntegerField(default=0)),
            ],
            options={
                "db_table": "identity_user",
            },
        ),
        migrations.CreateModel(
            name="UserSession",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sessions",
                        to="identity.user",
                    ),
                ),
                ("refresh_jti", models.UUIDField(unique=True)),
                ("device_label", models.CharField(blank=True, max_length=200)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("last_used_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "identity_user_session",
            },
        ),
        migrations.CreateModel(
            name="Follow",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "follower",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="following_set",
                        to="identity.user",
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="followers_set",
                        to="identity.user",
                    ),
                ),
            ],
            options={
                "db_table": "identity_follow",
                "unique_together": {("follower", "target")},
            },
        ),
        migrations.CreateModel(
            name="UserPreferences",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="preferences",
                        to="identity.user",
                    ),
                ),
                ("language", models.CharField(default="en-US", max_length=10)),
                ("theme", models.CharField(default="system", max_length=20)),
                ("timezone", models.CharField(default="UTC", max_length=50)),
                ("email_enabled", models.BooleanField(default=True)),
                ("push_enabled", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "identity_user_preferences",
            },
        ),
        migrations.CreateModel(
            name="KycProfile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kyc_profile",
                        to="identity.user",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("not_submitted", "not_submitted"),
                            ("pending", "pending"),
                            ("approved", "approved"),
                            ("rejected", "rejected"),
                        ],
                        default="not_submitted",
                        max_length=20,
                    ),
                ),
                ("full_name", models.CharField(blank=True, max_length=200)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                ("nationality", models.CharField(blank=True, max_length=3)),
                ("id_type", models.CharField(blank=True, max_length=30)),
                ("id_number", models.CharField(blank=True, max_length=100)),
                ("id_expiry_date", models.DateField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("reject_reason", models.TextField(blank=True)),
            ],
            options={
                "db_table": "identity_kyc_profile",
            },
        ),
        migrations.CreateModel(
            name="KycDocument",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kyc_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to="identity.kycprofile",
                    ),
                ),
                (
                    "document_type",
                    models.CharField(
                        choices=[("id_front", "id_front"), ("selfie", "selfie")],
                        max_length=20,
                    ),
                ),
                ("image_url", models.URLField()),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "identity_kyc_document",
                "unique_together": {("kyc_profile", "document_type")},
            },
        ),
        migrations.CreateModel(
            name="CreatorProfile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="creator_profile",
                        to="identity.user",
                    ),
                ),
                ("bio_extended", models.TextField(blank=True)),
                ("categories", models.JSONField(default=list)),
                ("social_links", models.JSONField(default=dict)),
                ("is_verified", models.BooleanField(default=False)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "identity_creator_profile",
            },
        ),
        migrations.CreateModel(
            name="PasswordResetToken",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reset_tokens",
                        to="identity.user",
                    ),
                ),
                ("token_hash", models.CharField(max_length=256, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "identity_password_reset_token",
            },
        ),
    ]
