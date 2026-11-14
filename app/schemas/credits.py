"""Contract for data citations: `data_credits` on responses and GET /credits."""

from pydantic import BaseModel, ConfigDict, Field


class DataCredit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(examples=["spherex_dr1"])
    source: str = Field(description="Mission and archive", examples=["SPHEREx (NASA/JPL/Caltech), served by IRSA/IPAC"])
    dataset: str
    used_for: str = Field(description="What this API uses it for")
    citation: str | None = Field(default=None, description="Paper to cite, when there is one")
    doi: str | None = Field(default=None, examples=["10.26131/IRSA678"])
    url: str = Field(description="DOI landing page or archive")
    access: str | None = Field(default=None, description="Service the data comes from")


class CreditsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_credits: list[DataCredit]
    acknowledgement: str = Field(description="SPHEREx acknowledgement text requested by IRSA")
    acknowledgement_source: str
    software: list[str]
