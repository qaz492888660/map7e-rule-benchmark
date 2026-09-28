def legacy_upload(request, storage):
    """Legacy path; accepts the request body without checking a size limit."""
    return storage.save(request.body)
