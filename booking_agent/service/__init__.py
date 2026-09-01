"""The agent behind an HTTP interface.

This is the only package that knows there is a web server, and the only one
that reads a clock. Everything under clinic/, conversation/ and channels/ takes
the time as an argument, which is what makes a hold expiring mid-sentence a
three-line test rather than a ten-minute wait — and the price of that is that
somewhere the real time has to be read. Here is that somewhere.
"""

from booking_agent.service.api import build

__all__ = ["build"]
