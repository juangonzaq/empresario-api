from rest_framework.throttling import SimpleRateThrottle


class LeadThrottle(SimpleRateThrottle):
    """El formulario es público: sin freno, un bot llena la bandeja en minutos."""

    scope = "leads"

    def get_cache_key(self, request, view):
        return f"throttle_leads_{self.get_ident(request)}"
