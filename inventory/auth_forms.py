from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm


class WarehouseAuthenticationForm(AuthenticationForm):
    """Authenticate by canonical username or a unique account email address."""

    def clean(self):
        identifier = (self.cleaned_data.get("username") or "").strip()
        if identifier:
            user_model = get_user_model()
            username_matches = list(
                user_model._default_manager.filter(username__iexact=identifier)
                .values_list("username", flat=True)[:2]
            )
            if len(username_matches) == 1:
                identifier = username_matches[0]
            elif not username_matches:
                email_matches = list(
                    user_model._default_manager.filter(email__iexact=identifier)
                    .values_list("username", flat=True)[:2]
                )
                if len(email_matches) == 1:
                    identifier = email_matches[0]
            self.cleaned_data["username"] = identifier
        return super().clean()
