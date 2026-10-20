"""An agent that takes booking calls for a clinic.

Six packages, and the direction between them is the design:

    clinic/        what is offered and when it is free. Knows nothing about
                   conversations.
    conversation/  what to say next. Knows about the clinic; knows nothing
                   about telephones, web servers or protocols.
    channels/      how to say it. Knows nothing about why.
    service/       the outside world, and the only clock in the building.
    telephony/     a switchboard, as a client of the service.
    mcp/           the clinic as tools for a model, beside the service and
                   not on it.

Nothing above imports anything below it, and there is a test that says so.
"""

__version__ = "1.1.0"
__author__ = "Riccardo Sapuppo"
