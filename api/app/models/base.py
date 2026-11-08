from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """JSON fields are camelCase; Python attributes are snake_case."""

    model_config = ConfigDict(alias_generator=to_camel)
