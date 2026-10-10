from enum import Enum
from dataclasses import dataclass

class ToolResultStatus(str, Enum):
    OK = "ok"
    POLICY_REJECTION = "policy_rejection"
    INVALID_ARG = "invalid_arg"
    ERROR = "error"

@dataclass
class ToolExecutionResult:
    status: ToolResultStatus
    output: str

    @property
    def is_success(self) -> bool:
        return self.status == ToolResultStatus.OK

    @property
    def is_policy_rejection(self) -> bool:
        return self.status == ToolResultStatus.POLICY_REJECTION

    @property
    def is_invalid_arg(self) -> bool:
        return self.status == ToolResultStatus.INVALID_ARG

    def __str__(self) -> str:
        return self.output

    def __contains__(self, item: str) -> bool:
        return item in self.output

    def startswith(self, prefix: str) -> bool:
        return self.output.startswith(prefix)

    def endswith(self, suffix: str) -> bool:
        return self.output.endswith(suffix)

    def strip(self) -> str:
        return self.output.strip()

    def find(self, *args, **kwargs) -> int:
        return self.output.find(*args, **kwargs)

    def rfind(self, *args, **kwargs) -> int:
        return self.output.rfind(*args, **kwargs)

    def count(self, *args, **kwargs) -> int:
        return self.output.count(*args, **kwargs)

    def __getitem__(self, item):
        return self.output[item]

    def __iter__(self):
        yield self.is_success
        yield self.is_policy_rejection
        yield self.output
