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


class DiscoveryRun(models.Model):
    """One discovery, and what happened, step by step - shown as a timeline on
    the discovered devices page (see discovery/runs.py): "Discover adjacent
    devices" (the listener on RouterFleet's LAN, for a while), or the
    neighbour tables of the managed devices an operator chose."""
    KINDS = (('lan', "RouterFleet's LAN"), ('devices', 'Chosen managed devices'))
    STATES = (('waiting', 'Waiting for the listener'), ('listening', 'Listening'),
              ('reading', 'Reading neighbour tables'), ('done', 'Finished'), ('failed', 'Not started'))
    kind = models.CharField(max_length=8, choices=KINDS, default='lan')
    # For 'devices': the uuids of the chosen managed devices, in order
    targets = models.JSONField(default=list, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    until = models.DateTimeField()
    requested_by_name = models.CharField(max_length=150, blank=True)
    state = models.CharField(max_length=10, choices=STATES, default='waiting')
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # Keys of the devices heard in this run, and the timeline: [{when, text, state}]
    heard = models.JSONField(default=list, blank=True)
    events = models.JSONField(default=list, blank=True)
    uuid = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)

    class Meta:
        ordering = ['-created']

    @property
    def active(self):
        return self.state in ('waiting', 'listening', 'reading')
