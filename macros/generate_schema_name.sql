{#
  Use the configured schema name as-is (raw, staging, intermediate, marts)
  instead of dbt's default "<target>_<schema>" prefixing, so the dashboard
  and the monitors always read marts.<table>.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
