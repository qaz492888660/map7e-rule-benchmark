MAX_UPLOAD_BYTES = 5 * 1024 * 1024

def upload(request, storage):
    if request.content_length > MAX_UPLOAD_BYTES:
        raise ValueError("upload too large")
    return storage.save(request.body)
