def sign_up(identity, password):
    user = create_account(identity, password)
    return issue_session(user)


def sign_in(identity, password):
    user = verify_credentials(identity, password)
    return issue_session(user)


def create_account(identity, password):
    return {"identity": identity, "password": password}


def verify_credentials(identity, password):
    return {"identity": identity}


def issue_session(user):
    return {"session": user["identity"]}


def google_oauth_calendar(user, authorization_code):
    user["calendar_token"] = authorization_code
    return {"calendar_connected": True}


def authenticate_internal_service(api_key):
    return api_key == "fixture-service"
