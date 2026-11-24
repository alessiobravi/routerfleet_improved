import uuid

from django.db import models


class DiscoveredDevice(models.Model):
    """A device seen by a neighbour protocol (LLDP, CDP, MikroTik MNDP): in the
    neighbour table of a managed device, or by the optional listener on
    RouterFleet's own LAN segment. One row per device - by its MAC address
    when known, else by its address and name."""
    key = models.CharField(max_length=160, unique=True)
    identity = models.CharField(max_length=200, blank=True)
    address = models.CharField(max_length=100, blank=True)
    mac = models.CharField(max_length=17, blank=True)
    platform = models.CharField(max_length=200, blank=True)
    model = models.CharField(max_length=200, blank=True)
    version = models.CharField(max_length=200, blank=True)
    protocols = models.CharField(max_length=40, blank=True)
    # Where it was seen: a managed device and its port, or RouterFleet's LAN
    seen_by = models.CharField(max_length=200, blank=True)
    seen_by_router = models.ForeignKey('router_manager.Router', null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name='+')
    interface = models.CharField(max_length=100, blank=True)
    remote_interface = models.CharField(max_length=100, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField()
    # Not of interest: kept, but out of the list of devices to add
    dismissed = models.BooleanField(default=False)
    # Whether RouterFleet's host reaches it: its SSH port answering (None: not
    # checked yet), what the check found, and when
    reachable = models.BooleanField(null=True, blank=True)
    reachable_detail = models.CharField(max_length=120, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    class Meta:
        ordering = ['-last_seen']

    def __str__(self):
        return self.identity or self.address or self.mac
