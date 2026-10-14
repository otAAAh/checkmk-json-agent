# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Request guards shared by the Explorer's AJAX pages.

Checkmk does not apply these centrally: every AJAX page with an effect checks the
CSRF token itself (``cmk/gui/views/page_ajax_reschedule.py``), and code that
reveals a stored password must first ask whether the user may use that entry
(``passwordstore_choices``) — the Password FormSpec's visitor does not.

Both helpers moved between Checkmk releases, so each tries the current location
and falls back to the 2.5 one.
"""

from __future__ import annotations

from cmk.gui.i18n import _
from cmk.gui.pages import PageContext


def check_csrf(ctx: PageContext) -> None:
    """Raise unless the request carries the session's CSRF token (``_csrf_token``)."""
    try:
        from cmk.web.utils.csrf_token import check_csrf_token  # 3.0+
    except ImportError:
        from cmk.gui.utils.csrf_token import check_csrf_token as check_csrf_token_2_5

        check_csrf_token_2_5()
        return
    check_csrf_token(ctx.session, ctx.request, i18n=_)


def usable_password_ids() -> set[str]:
    """The password-store entries the logged-in user may use.

    The same filter that limits the choices a Password field offers: entries the
    user may edit plus those shared with one of their contact groups, or all of
    them with "Write access to all passwords".
    """
    from cmk.gui.watolib.password_store import passwordstore_choices

    return {ident for ident, _title in passwordstore_choices() if ident}
