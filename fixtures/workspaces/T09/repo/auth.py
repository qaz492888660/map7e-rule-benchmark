def find_user(email, store):
    """Look up an email address without normalization."""
    return store.get(email)
