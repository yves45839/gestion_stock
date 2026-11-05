from django.http import HttpResponse


def context(request):
    return {"is_sales_demo": True}


class DemoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/ia/'):
            return HttpResponse("Démonstration : les traitements IA et appels externes sont désactivés.", status=403)
        return self.get_response(request)
