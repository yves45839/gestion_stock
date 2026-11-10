from io import BytesIO
from tempfile import TemporaryDirectory

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import UserProfile


def photo_upload(fmt="PNG"):
    data = BytesIO()
    Image.new("RGB", (720, 540), "navy").save(data, format=fmt)
    return SimpleUploadedFile("photo." + fmt.lower(), data.getvalue(), content_type="image/" + fmt.lower())


class ProfilePhotoTests(TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=folder))
        self.user = get_user_model().objects.create_user(
            username="Anderson", password="test-profile", first_name="Anderson", last_name="Abobo",
        )
        self.other = get_user_model().objects.create_user(username="autre")
        self.url = reverse("inventory:user_profile")
        self.client.force_login(self.user)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, {"avatar": photo_upload()}).status_code, 302)
        self.assertFalse(UserProfile.objects.exists())

    def test_username_only_and_default_avatar_without_creating_profile_on_get(self):
        response = self.client.get(self.url)
        self.assertContains(response, '<span class="profile-name">Anderson</span>', html=True)
        self.assertNotContains(response, "Anderson Abobo")
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertFalse(UserProfile.objects.exists())

    def test_photo_is_normalized_and_shown_in_header(self):
        response = self.client.post(self.url, {"avatar": photo_upload()})
        self.assertRedirects(response, self.url)
        profile = UserProfile.objects.get(user=self.user)
        with profile.avatar.open("rb") as stream, Image.open(stream) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (512, 384))
        self.assertContains(self.client.get(self.url), profile.avatar.url)

    def test_upload_cannot_change_someone_elses_profile(self):
        self.client.post(self.url, {"avatar": photo_upload(), "user": self.other.pk})
        self.assertTrue(UserProfile.objects.filter(user=self.user).exists())
        self.assertFalse(UserProfile.objects.filter(user=self.other).exists())

    def test_invalid_or_oversized_photo_preserves_current_image(self):
        self.client.post(self.url, {"avatar": photo_upload()})
        original = UserProfile.objects.get(user=self.user).avatar.name
        oversized = photo_upload()
        oversized = SimpleUploadedFile("large.png", oversized.read() + b"x" * (5 * 1024 * 1024), content_type="image/png")
        for upload in [SimpleUploadedFile("fake.png", b"not an image", content_type="image/png"), photo_upload("GIF"), oversized]:
            with self.subTest(filename=upload.name):
                response = self.client.post(self.url, {"avatar": upload})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(UserProfile.objects.get(user=self.user).avatar.name, original)

    def test_replacement_and_removal_delete_old_photo_after_commit(self):
        self.client.post(self.url, {"avatar": photo_upload()})
        original = UserProfile.objects.get(user=self.user).avatar
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {"avatar": photo_upload()})
        updated = UserProfile.objects.get(user=self.user).avatar
        self.assertNotEqual(original.name, updated.name)
        self.assertFalse(original.storage.exists(original.name))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {"remove_avatar": "on"})
        self.assertFalse(UserProfile.objects.get(user=self.user).avatar)
        self.assertFalse(updated.storage.exists(updated.name))
        self.assertNotContains(self.client.get(self.url), updated.url)

    def test_conflicting_actions_do_not_replace_photo(self):
        response = self.client.post(self.url, {"avatar": photo_upload(), "remove_avatar": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertFalse(UserProfile.objects.exists())
