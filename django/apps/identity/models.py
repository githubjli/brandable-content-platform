"""Models for identity.

All models must:
  - inherit from AbstractBaseModel (UUID pk, created_at, updated_at)
  - use Decimal for money fields (Decimal(18,4))
  - never reference models from other apps directly
"""

from django.db.models import (
    CASCADE,
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    EmailField,
    ForeignKey,
    GenericIPAddressField,
    JSONField,
    OneToOneField,
    PositiveIntegerField,
    TextField,
    URLField,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel


class User(AbstractBaseModel):
    """Platform user.  Uses our own password hashing — NOT Django's AbstractUser."""

    email = EmailField(unique=True)  # normalized: lowercase + stripped
    password_hash = CharField(max_length=256)  # Django password hasher format
    display_name = CharField(max_length=100)
    first_name = CharField(max_length=100, blank=True)
    last_name = CharField(max_length=100, blank=True)
    avatar_url = URLField(null=True, blank=True)
    bio = TextField(blank=True)
    is_active = BooleanField(default=True)
    is_creator = BooleanField(default=False)
    is_seller = BooleanField(default=False)
    is_admin = BooleanField(default=False)
    follower_count = PositiveIntegerField(default=0)
    following_count = PositiveIntegerField(default=0)

    class Meta:
        db_table = "identity_user"
        indexes = []

    def __str__(self) -> str:
        return f"User({self.email})"


class UserSession(AbstractBaseModel):
    """Tracks one active refresh token per device/login.

    Deleting this row immediately invalidates the corresponding refresh JWT.
    """

    user = ForeignKey(User, on_delete=CASCADE, related_name="sessions")
    refresh_jti = UUIDField(unique=True)  # jti claim of the refresh token
    device_label = CharField(max_length=200, blank=True)
    ip_address = GenericIPAddressField(null=True, blank=True)
    last_used_at = DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_user_session"

    def __str__(self) -> str:
        return f"UserSession(user={self.user_id}, jti={self.refresh_jti})"


class Follow(AbstractBaseModel):
    """Directional follow relationship between two users."""

    follower = ForeignKey(User, on_delete=CASCADE, related_name="following_set")
    target = ForeignKey(User, on_delete=CASCADE, related_name="followers_set")

    class Meta:
        db_table = "identity_follow"
        unique_together = [("follower", "target")]

    def __str__(self) -> str:
        return f"Follow({self.follower_id} -> {self.target_id})"


class UserPreferences(AbstractBaseModel):
    """Per-user notification and localisation preferences."""

    user = OneToOneField(User, on_delete=CASCADE, related_name="preferences")
    language = CharField(max_length=10, default="en-US")
    theme = CharField(max_length=20, default="system")
    timezone = CharField(max_length=50, default="UTC")
    email_enabled = BooleanField(default=True)
    push_enabled = BooleanField(default=False)

    class Meta:
        db_table = "identity_user_preferences"

    def __str__(self) -> str:
        return f"UserPreferences(user={self.user_id})"


class KycProfile(AbstractBaseModel):
    """KYC state machine: not_submitted → pending → approved | rejected."""

    STATUS = [
        ("not_submitted", "not_submitted"),
        ("pending", "pending"),
        ("approved", "approved"),
        ("rejected", "rejected"),
    ]

    user = OneToOneField(User, on_delete=CASCADE, related_name="kyc_profile")
    status = CharField(max_length=20, choices=STATUS, default="not_submitted")
    full_name = CharField(max_length=200, blank=True)
    date_of_birth = DateField(null=True, blank=True)
    nationality = CharField(max_length=3, blank=True)  # ISO 3166-1 alpha-2/3
    id_type = CharField(max_length=30, blank=True)  # passport / national_id / ...
    id_number = CharField(max_length=100, blank=True)
    id_expiry_date = DateField(null=True, blank=True)
    submitted_at = DateTimeField(null=True, blank=True)
    reviewed_at = DateTimeField(null=True, blank=True)
    reject_reason = TextField(blank=True)

    class Meta:
        db_table = "identity_kyc_profile"

    def __str__(self) -> str:
        return f"KycProfile(user={self.user_id}, status={self.status})"


class KycDocument(AbstractBaseModel):
    """Individual KYC document uploaded by the user."""

    TYPES = [
        ("id_front", "id_front"),
        ("selfie", "selfie"),
    ]

    kyc_profile = ForeignKey(KycProfile, on_delete=CASCADE, related_name="documents")
    document_type = CharField(max_length=20, choices=TYPES)
    image_url = URLField()
    uploaded_at = DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_kyc_document"
        unique_together = [("kyc_profile", "document_type")]

    def __str__(self) -> str:
        return f"KycDocument(profile={self.kyc_profile_id}, type={self.document_type})"


class CreatorProfile(AbstractBaseModel):
    """1:1 extension of User for creator-specific metadata.

    Created when user is approved as creator.
    """

    user = OneToOneField(User, on_delete=CASCADE, related_name="creator_profile")
    bio_extended = TextField(blank=True)
    categories = JSONField(default=list)
    social_links = JSONField(default=dict)
    is_verified = BooleanField(default=False)
    verified_at = DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_creator_profile"

    def __str__(self) -> str:
        return f"CreatorProfile(user={self.user_id})"


class PasswordResetToken(AbstractBaseModel):
    """Single-use password reset token (raw token is emailed; only SHA-256 hash stored)."""

    user = ForeignKey(User, on_delete=CASCADE, related_name="reset_tokens")
    token_hash = CharField(max_length=256, unique=True)  # SHA-256 of raw token
    expires_at = DateTimeField()
    used_at = DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_password_reset_token"

    def __str__(self) -> str:
        return f"PasswordResetToken(user={self.user_id}, expires={self.expires_at})"
