"""Adapters that get messages out of the process.

The one package in the codebase that talks to something outside the database.
Everything here sits behind the ``NotificationChannel`` port, so the application
and domain layers name none of it.
"""
