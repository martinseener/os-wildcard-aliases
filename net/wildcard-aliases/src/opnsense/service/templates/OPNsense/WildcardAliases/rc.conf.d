{% if helpers.exists('OPNsense.WildcardAliases.general.enabled') and OPNsense.WildcardAliases.general.enabled == '1' %}
wildcardaliases_enable="YES"
{% else %}
wildcardaliases_enable="NO"
{% endif %}
