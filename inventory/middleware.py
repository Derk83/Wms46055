class ProxySSLHeaderMiddleware:
    """Ensure Django sees HTTPS when behind Nginx Proxy Manager."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if "HTTP_X_FORWARDED_PROTO" not in request.META:
            request.META["HTTP_X_FORWARDED_PROTO"] = "https"
        return self.get_response(request)


class HostURLConfMiddleware:
    """Strictly isolate the focused request portal from warehouse URLs."""

    request_host = "requests.rplwms.com"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":", 1)[0].lower().rstrip(".")
        request.is_request_portal = host == self.request_host
        request.is_wms_host = host == "bbx.rplwms.com"
        if request.is_request_portal:
            request.urlconf = "inventory.request_urls"
        return self.get_response(request)
