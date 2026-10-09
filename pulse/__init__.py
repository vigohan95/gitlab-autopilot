"""pulse — a tiny site-health probe service.

It is the application that Autopilot operates on. It is deliberately small but
real: an HTTP API, a persistent store, outbound probes, a container image, a
status page and Prometheus metrics, so that every DevSecOps stage has
something genuine to act on.
"""

__version__ = "0.1.2"
