from django import template

from backoffice.forms import as_percent

register = template.Library()


@register.filter
def percent(rate):
    """0.1 -> «10 %», 0.125 -> «12,5 %»."""
    return f"{as_percent(rate).replace('.', ',')} %"
