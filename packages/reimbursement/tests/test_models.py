from uuid import UUID, uuid4

import pytest
from reimbursement.models import Reimbursement


class DescribeReimbursement:
    def it_decodes_a_json_text_original_payload_from_a_record(self) -> None:
        record = {"uuid": uuid4(), "original_payload": '{"claimed_amount_brl": 93.5}'}

        reimbursement = Reimbursement.from_record(record)

        assert reimbursement.original_payload == {"claimed_amount_brl": 93.5}

    def it_round_trips_the_uuid_as_a_uuid(self) -> None:
        uuid = uuid4()
        record = {"uuid": uuid, "original_payload": "{}"}

        reimbursement = Reimbursement.from_record(record)

        assert reimbursement.uuid == uuid
        assert isinstance(reimbursement.uuid, UUID)

    def it_raises_on_malformed_original_payload_text(self) -> None:
        record = {"uuid": uuid4(), "original_payload": "{not valid json"}

        with pytest.raises(ValueError):
            Reimbursement.from_record(record)
