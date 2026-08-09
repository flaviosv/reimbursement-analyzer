from pydantic import BaseModel

from api.reimbursement.response import ReimbursementItem


class ReimbursementListResponse(BaseModel):
    msg: str
    data: list[ReimbursementItem]
