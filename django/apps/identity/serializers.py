"""Serializers for identity.

Request serializers validate incoming data.
Response serializers format outgoing data from service-layer dicts.
No business logic here — call services, not models directly.
"""

from rest_framework import serializers

# ---------------------------------------------------------------------------
# Request serializers
# ---------------------------------------------------------------------------


class RegisterRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    display_name = serializers.CharField(max_length=100)
    first_name = serializers.CharField(max_length=100, required=False, default="", allow_blank=True)
    last_name = serializers.CharField(max_length=100, required=False, default="", allow_blank=True)


class LoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    device_label = serializers.CharField(
        max_length=200, required=False, default="", allow_blank=True
    )
    totp_code = serializers.CharField(max_length=10, required=False, default="", allow_blank=True)


class TwoFactorEnableSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=10)


class TwoFactorDisableSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=10)


class RefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(min_length=8, write_only=True)
    revoke_other_sessions = serializers.BooleanField(required=False, default=False)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    reset_token = serializers.CharField()
    new_password = serializers.CharField(min_length=8, write_only=True)


class EmailVerificationConfirmSerializer(serializers.Serializer):
    verification_token = serializers.CharField()


class ProfileUpdateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=100, required=False)
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)
    avatar_url = serializers.URLField(required=False, allow_null=True)
    # avatar (file upload) and avatar_clear handled by view


class PreferencesSerializer(serializers.Serializer):
    language = serializers.CharField(max_length=10, required=False)
    theme = serializers.CharField(max_length=20, required=False)
    timezone = serializers.CharField(max_length=50, required=False)
    email_enabled = serializers.BooleanField(required=False)
    push_enabled = serializers.BooleanField(required=False)


class KycUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200, required=False)
    date_of_birth = serializers.DateField(required=False)
    nationality = serializers.CharField(max_length=3, required=False)
    id_type = serializers.CharField(max_length=30, required=False)
    id_number = serializers.CharField(max_length=100, required=False)
    id_expiry_date = serializers.DateField(required=False)


class KycDocumentUploadSerializer(serializers.Serializer):
    document_type = serializers.ChoiceField(choices=["id_front", "selfie"])
    image = serializers.ImageField()


class CreatorProfileUpdateSerializer(serializers.Serializer):
    bio_extended = serializers.CharField(required=False, allow_blank=True)
    categories = serializers.ListField(child=serializers.CharField(), required=False)
    social_links = serializers.DictField(child=serializers.URLField(), required=False)


# ---------------------------------------------------------------------------
# Response serializers
# ---------------------------------------------------------------------------


class TokensSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    expires_at = serializers.CharField()


class UserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    display_name = serializers.CharField()
    avatar_url = serializers.URLField(allow_null=True)
    is_creator = serializers.BooleanField()
    is_admin = serializers.BooleanField()
    created_at = serializers.CharField()


class MeUserSerializer(UserSerializer):
    kyc_status = serializers.CharField()
    session_id = serializers.UUIDField(allow_null=True)


class SessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    device_label = serializers.CharField(allow_null=True)
    ip_address = serializers.CharField(allow_null=True)
    last_used_at = serializers.CharField()
    created_at = serializers.CharField()
    is_current = serializers.BooleanField()


class CreatorProfileNestedSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    bio_extended = serializers.CharField()
    categories = serializers.ListField(child=serializers.CharField())
    social_links = serializers.DictField()
    is_verified = serializers.BooleanField()
    verified_at = serializers.CharField(allow_null=True)
    created_at = serializers.CharField()


class ProfileStatsSerializer(serializers.Serializer):
    total_videos = serializers.IntegerField()
    total_dramas = serializers.IntegerField()
    total_likes_received = serializers.IntegerField()
    total_views_received = serializers.IntegerField()
    total_gifts_received_amount = serializers.CharField()
    total_gifts_received_currency = serializers.CharField()


class ProfileSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    display_name = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    avatar_url = serializers.URLField(allow_null=True)
    bio = serializers.CharField()
    is_creator = serializers.BooleanField()
    is_seller = serializers.BooleanField()
    is_admin = serializers.BooleanField()
    follower_count = serializers.IntegerField()
    following_count = serializers.IntegerField()
    creator_profile = CreatorProfileNestedSerializer(allow_null=True)
    stats = ProfileStatsSerializer()
    created_at = serializers.CharField()


class ViewerContextSerializer(serializers.Serializer):
    is_following = serializers.BooleanField()
    is_self = serializers.BooleanField()


class PublicUserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    display_name = serializers.CharField()
    avatar_url = serializers.URLField(allow_null=True)
    bio = serializers.CharField()
    is_creator = serializers.BooleanField()
    creator_profile = CreatorProfileNestedSerializer(allow_null=True)
    follower_count = serializers.IntegerField()
    following_count = serializers.IntegerField()
    viewer_context = ViewerContextSerializer(allow_null=True)
    created_at = serializers.CharField()


class FollowResponseSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    is_following = serializers.BooleanField()
    follower_count = serializers.IntegerField()


class KycDocumentSerializer(serializers.Serializer):
    document_type = serializers.CharField()
    image_url = serializers.URLField()
    uploaded_at = serializers.CharField()


class KycProfileSerializer(serializers.Serializer):
    status = serializers.CharField()
    full_name = serializers.CharField(allow_null=True)
    date_of_birth = serializers.CharField(allow_null=True)
    nationality = serializers.CharField(allow_null=True)
    id_type = serializers.CharField(allow_null=True)
    id_number = serializers.CharField(allow_null=True)
    id_expiry_date = serializers.CharField(allow_null=True)
    submitted_at = serializers.CharField(allow_null=True)
    reviewed_at = serializers.CharField(allow_null=True)
    reject_reason = serializers.CharField(allow_null=True)
    documents = serializers.DictField()


class CreatorProfileSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    bio_extended = serializers.CharField()
    categories = serializers.ListField(child=serializers.CharField())
    social_links = serializers.DictField()
    is_verified = serializers.BooleanField()
    verified_at = serializers.CharField(allow_null=True)
    kyc_status = serializers.CharField()
    created_at = serializers.CharField()
