"""An agent that takes booking calls for a clinic.

Four packages, and the direction between them is the design:

    clinic/        what is offered and when it is free. Knows nothing about
                   conversations.
    conversation/  what to say next. Knows about the clinic; knows nothing
                   about telephones or web servers.
    channels/      how to say it. Knows nothing about why.
    service/       the outside world, and the only clock in the building.

Nothing above imports anything below it, and there is a test that says so.
"""

__version__ = "1.0.0"
__author__ = "Riccardo Sapuppo"
