#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""An endpoint's 'Authentication' choice: basic, bearer token, OAuth 2.0 client
credentials, or an API key in a header or a query parameter.
"""

import re

from cmk.rulesets.v1 import Help, Message, Title
from cmk.rulesets.v1.form_specs import (
    CascadingSingleChoice,
    CascadingSingleChoiceElement,
    DefaultValue,
    DictElement,
    Dictionary,
    FieldSize,
    InputHint,
    Password,
    SingleChoice,
    SingleChoiceElement,
    String,
    migrate_to_password,
    validators,
)

from .common import _validate_query_parameter, _validate_url

# An HTTP field name as RFC 9110 defines it (a token): letters, digits and a
# handful of symbols, no whitespace. Checked in Setup because requests would
# otherwise raise deep inside the agent, where the error reaches the user as an
# unreachable endpoint rather than as "fix this field".
# '\Z', not '$': '$' also matches before a trailing newline, which would let
# 'X-Api-Key\n' through - and a newline in a header name is header injection.
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def _validate_header_name(value: str) -> None:
    if not _HEADER_NAME_PATTERN.match(value):
        raise validators.ValidationError(
            Message("Enter a valid HTTP header name, e.g. 'X-API-Key'.")
        )


def _authentication() -> CascadingSingleChoice:
    return CascadingSingleChoice(
        title=Title("Authentication"),
        prefill=DefaultValue("auth_token"),
        elements=[
            CascadingSingleChoiceElement(
                name="auth_login",
                title=Title("Basic authentication (username / password)"),
                parameter_form=Dictionary(
                    elements={
                        "username": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Username"),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                        "password": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("Password"),
                                migrate=migrate_to_password,
                            ),
                        ),
                    }
                ),
            ),
            CascadingSingleChoiceElement(
                name="auth_token",
                title=Title("Bearer token"),
                parameter_form=Dictionary(
                    elements={
                        "token": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("Token"),
                                help_text=Help("Sent as 'Authorization: Bearer <token>'."),
                                migrate=migrate_to_password,
                            ),
                        ),
                    }
                ),
            ),
            CascadingSingleChoiceElement(
                name="auth_oauth2",
                title=Title("OAuth 2.0 (client credentials)"),
                parameter_form=Dictionary(
                    help_text=Help(
                        "The machine-to-machine OAuth 2.0 grant: the agent exchanges a "
                        "client ID and secret for a short-lived access token and sends "
                        "it as 'Authorization: Bearer <token>'. The token is cached "
                        "until shortly before it expires, so a rule polling every "
                        "minute does not ask the identity provider every minute. No "
                        "browser and no user consent are involved - if your API only "
                        "works with a token obtained by a person logging in, this is "
                        "not the right mode."
                    ),
                    elements={
                        "token_url": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Token URL"),
                                field_size=FieldSize.LARGE,
                                macro_support=True,
                                help_text=Help(
                                    "The identity provider's token endpoint, e.g. "
                                    "'https://login.example.com/oauth2/v2.0/token'. "
                                    "This is NOT the API URL above."
                                ),
                                prefill=InputHint("https://login.example.com/oauth2/v2.0/token"),
                                custom_validate=(_validate_url,),
                            ),
                        ),
                        "client_id": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Client ID"),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                        "client_secret": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("Client secret"),
                                migrate=migrate_to_password,
                            ),
                        ),
                        "scope": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("Scope"),
                                help_text=Help(
                                    "Space-separated scopes to request, if the provider "
                                    "needs them - e.g. 'api://monitoring/.default'."
                                ),
                            ),
                        ),
                        "audience": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("Audience"),
                                help_text=Help(
                                    "Sent as the 'audience' parameter. Required by some "
                                    "providers (Auth0, for instance) to say which API "
                                    "the token is for; leave empty otherwise."
                                ),
                            ),
                        ),
                        "client_auth": DictElement(
                            required=False,
                            parameter_form=SingleChoice(
                                title=Title("How to send the client credentials"),
                                help_text=Help(
                                    "RFC 6749 allows both, and providers disagree about "
                                    "which they accept - a wrong choice here shows up as "
                                    "an unhelpful 401 from the token URL. Try the "
                                    "Authorization header first; switch to the request "
                                    "body if the provider rejects it."
                                ),
                                elements=[
                                    SingleChoiceElement(
                                        name="basic",
                                        title=Title("In the Authorization header (HTTP basic)"),
                                    ),
                                    SingleChoiceElement(
                                        name="post",
                                        title=Title("In the request body"),
                                    ),
                                ],
                                prefill=DefaultValue("basic"),
                            ),
                        ),
                    },
                ),
            ),
            CascadingSingleChoiceElement(
                name="auth_header",
                title=Title("API key in a request header"),
                parameter_form=Dictionary(
                    elements={
                        "header": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Header name"),
                                help_text=Help(
                                    "Name of the header carrying the key, e.g. "
                                    "'X-API-Key', 'apikey' or 'PRIVATE-TOKEN'. For "
                                    "'Authorization: Bearer <token>' use the bearer "
                                    "token choice instead."
                                ),
                                prefill=DefaultValue("X-API-Key"),
                                custom_validate=(_validate_header_name,),
                            ),
                        ),
                        "key": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("API key"),
                                help_text=Help(
                                    "Kept in the Checkmk password store. Unlike a key "
                                    "typed into 'Additional request headers' it is "
                                    "never written to the configuration or to the "
                                    "agent's command line in clear text, and it can be "
                                    "rotated in one place."
                                ),
                                migrate=migrate_to_password,
                            ),
                        ),
                    }
                ),
            ),
            CascadingSingleChoiceElement(
                name="auth_query",
                title=Title("API key in a query parameter"),
                parameter_form=Dictionary(
                    elements={
                        "parameter": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Parameter name"),
                                help_text=Help(
                                    "Name of the query parameter carrying the key, e.g. "
                                    "'api_key'. It is appended to the URL for the "
                                    "request only: the key is redacted wherever the "
                                    "agent reports a URL, and it never appears in the "
                                    "service name. Prefer a header where the API "
                                    "offers one - a key in the URL is visible to "
                                    "proxies and server logs along the way."
                                ),
                                prefill=DefaultValue("api_key"),
                                custom_validate=(_validate_query_parameter,),
                            ),
                        ),
                        "key": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("API key"),
                                help_text=Help(
                                    "Kept in the Checkmk password store, so it is not "
                                    "written to the configuration or to the agent's "
                                    "command line in clear text - unlike a key typed "
                                    "into the URL."
                                ),
                                migrate=migrate_to_password,
                            ),
                        ),
                    }
                ),
            ),
        ],
    )
