import binascii
from typing import Literal
from bloodyAD import utils
from bloodyAD.utils import LOG
from bloodyAD.formatters import accesscontrol, common, dns, cryptography
from bloodyAD.exceptions import BloodyError
import ldap3


def dcsync(conn, trustee: str):
    """
    Removes DCSync right for provided trustee

    :param trustee: sAMAccountName, DN, GUID or SID of the trustee
    """
    new_sd, _ = utils.getSD(conn, conn.ldap.domainNC)
    if "s-1-" in trustee.lower():
        trustee_sid = trustee
    else:
        trustee_sid = next(conn.ldap.bloodysearch(trustee, attr="objectSid"))[
            "objectSid"
        ]
    access_mask = accesscontrol.ACCESS_FLAGS["ADS_RIGHT_DS_CONTROL_ACCESS"]
    utils.delRight(new_sd, trustee_sid, access_mask)

    controls = ldap3.protocol.microsoft.security_descriptor_control(
        sdflags=accesscontrol.DACL_SECURITY_INFORMATION
    )
    conn.ldap.bloodymodify(
        conn.ldap.domainNC,
        {"nTSecurityDescriptor": [ldap3.MODIFY_REPLACE, new_sd.getData()]},
        controls,
    )

    LOG.info(f"[-] {trustee} can't DCSync anymore")


def dnsRecord(
    conn,
    name: str,
    data: str,
    dnstype: Literal["A", "AAAA", "CNAME", "MX", "PTR", "SRV", "TXT"] = "A",
    zone: str = "CurrentDomain",
    ttl: int = None,
    preference: int = None,
    port: int = None,
    priority: int = None,
    weight: int = None,
    forest: bool = False,
):
    """
    Removes a DNS record of an AD environment.

    :param name: name of the dnsNode object (hostname) which contains the record
    :param data: DNS record data
    :param dnstype: DNS record type
    :param zone: DNS zone
    :param ttl: DNS record TTL
    :param preference: DNS MX record preference
    :param port: listening port of the service in a DNS SRV record
    :param priority: priority of a DNS SRV record against concurrent
    :param weight: weight of a DNS SRV record against concurrent
    :param forest: if set, will fetch the dns record in forest instead of domain
    """

    naming_context = "," + conn.ldap.domainNC
    if zone == "CurrentDomain":
        zone = ""
        for label in naming_context.split(",DC="):
            if label:
                zone += "." + label
        if forest:
            zone = "_msdcs" + zone
        else:
            # Removes first dot
            zone = zone[1:]

    # TODO: take into account custom ForestDnsZones and DomainDnsZones partition name ?
    if forest:
        zone_type = "ForestDnsZones"
    else:
        zone_type = "DomainDnsZones"

    zone_dn = f",DC={zone},CN=MicrosoftDNS,DC={zone_type}{naming_context}"
    record_dn = f"DC={name}{zone_dn}"

    record_to_remove = None
    for raw_record in next(
        conn.ldap.bloodysearch(record_dn, attr="dnsRecord", raw=True)
    )["dnsRecord"]:
        record = dns.Record(raw_record)
        tmp_record = dns.Record()

        if not ttl:
            ttl = record["TtlSeconds"]
        tmp_record.fromDict(
            data,
            dnstype,
            ttl,
            record["Rank"],
            record["Serial"],
            preference,
            port,
            priority,
            weight,
        )
        if tmp_record.getData() == raw_record:
            record_to_remove = raw_record
            break

    if not record_to_remove:
        LOG.warning("[!] Record not found")
        return

    conn.ldap.bloodymodify(
        record_dn, {"dnsRecord": (ldap3.MODIFY_DELETE, record_to_remove)}
    )

    LOG.info(f"[-] Given record has been successfully removed from {name}")


def genericAll(conn, target: str, trustee: str):
    """
    Removes full control of trustee on target

    :param target: sAMAccountName, DN, GUID or SID of the target
    :param trustee: sAMAccountName, DN, GUID or SID of the trustee
    """
    new_sd, _ = utils.getSD(conn, target)
    if "s-1-" in trustee.lower():
        trustee_sid = trustee
    else:
        trustee_sid = next(conn.ldap.bloodysearch(trustee, attr="objectSid"))[
            "objectSid"
        ]
    utils.delRight(new_sd, trustee_sid)

    controls = ldap3.protocol.microsoft.security_descriptor_control(
        sdflags=accesscontrol.DACL_SECURITY_INFORMATION
    )
    conn.ldap.bloodymodify(
        target,
        {"nTSecurityDescriptor": [ldap3.MODIFY_REPLACE, new_sd.getData()]},
        controls,
    )

    LOG.info(f"[-] {trustee} doesn't have GenericAll on {target} anymore")


def groupMember(conn, group: str, member: str):
    """
    Removes member (user, group, computer) from group

    :param group: sAMAccountName, DN, GUID or SID of the group
    :param member: sAMAccountName, DN, GUID or SID of the member
    """
    # This is equivalent to classic add member,
    # see [MS-ADTS] - 3.1.1.3.1.2.4 Alternative Forms of DNs
    # But <SID='sid'> also has the advantage of being compatible with foreign security principals,
    # see [MS-ADTS] - 3.1.1.5.3.3 Processing Specifics
    if "s-1-" in member.lower():
        # We assume member is an SID
        member_transformed = f"<SID={member}>"
    else:
        member_transformed = conn.ldap.dnResolver(member)

    conn.ldap.bloodymodify(group, {"member": (ldap3.MODIFY_DELETE, member_transformed)})
    LOG.info(f"[-] {member} removed from {group}")


def object(conn, target: str):
    """
    Removes object (user, group, computer, organizational unit, etc)

    :param target: sAMAccountName, DN, GUID or SID of the target
    """
    conn.ldap.bloodydelete(target)
    LOG.info(f"[-] {target} has been removed")


def rbcd(conn, target: str, service: str):
    """
    Removes Resource Based Constraint Delegation for service on target

    :param target: sAMAccountName, DN, GUID or SID of the target
    :param service: sAMAccountName, DN, GUID or SID of the service account
    """
    control_flag = 0
    new_sd, _ = utils.getSD(
        conn, target, "msDS-AllowedToActOnBehalfOfOtherIdentity", control_flag
    )
    if "s-1-" in service.lower():
        service_sid = service
    else:
        service_sid = next(conn.ldap.bloodysearch(service, attr="objectSid"))[
            "objectSid"
        ]
    access_mask = accesscontrol.ACCESS_FLAGS["ADS_RIGHT_DS_CONTROL_ACCESS"]
    utils.delRight(new_sd, service_sid, access_mask)

    attr_values = []
    if len(new_sd["Dacl"].aces) > 0:
        attr_values.append(new_sd.getData())
    conn.ldap.bloodymodify(
        target,
        {
            "msDS-AllowedToActOnBehalfOfOtherIdentity": [
                ldap3.MODIFY_REPLACE,
                attr_values,
            ]
        },
    )

    LOG.info(f"[-] {service} can't impersonate users on {target} anymore")


def shadowCredentials(conn, target: str, key: str = None):
    """
    Removes Key Credentials from target

    :param target: sAMAccountName, DN, GUID or SID of the target
    :param key: RSA key of Key Credentials to remove from the target, removes all if key not specified
    """
    keyCreds = next(
        conn.ldap.bloodysearch(target, attr="msDS-KeyCredentialLink", raw=True)
    )["msDS-KeyCredentialLink"]
    newKeyCreds = []
    isFound = False
    for keyCred in keyCreds:
        key_raw = common.DNBinary(keyCred).value
        key_blob = cryptography.KEYCREDENTIALLINK_BLOB(key_raw)
        if key and key_blob.getKeyID() != binascii.unhexlify(key):
            newKeyCreds.append(keyCred)
        else:
            isFound = True
            LOG.debug("[*] Key to delete found")

    if not isFound:
        LOG.warning("[!] No key found")

    conn.ldap.bloodymodify(
        target, {"msDS-KeyCredentialLink": [ldap3.MODIFY_REPLACE, newKeyCreds]}
    )

    str_key = key if key else "All keys"
    LOG.info(f"[-] {str_key} removed")


def uac(conn, target: str, f: list = None):
    """
    Removes property flags altering user/computer object behavior

    :param target: sAMAccountName, DN, GUID or SID of the target
    :param f: name of property flag to remove, can be called multiple times if multiple flags to remove (e.g -f LOCKOUT  -f ACCOUNTDISABLE)
    """
    uac = 0
    for flag in f:
        uac |= accesscontrol.ACCOUNT_FLAGS[flag]

    try:
        old_uac = next(
            conn.ldap.bloodysearch(target, attr="userAccountControl", raw=True)
        )["userAccountControl"][0]
    except IndexError as e:
        for allowed in next(conn.ldap.bloodysearch(target, attr="allowedAttributes"))[
            "allowedAttributes"
        ]:
            if "userAccountControl" in allowed:
                raise BloodyError(
                    "Current user doesn't have the right to read userAccountControl on"
                    f" {target}"
                ) from e
        raise BloodyError(f"{target} doesn't have userAccountControl attribute") from e

    uac = int(old_uac) & ~uac
    conn.ldap.bloodymodify(target, {"userAccountControl": [ldap3.MODIFY_REPLACE, uac]})

    LOG.info(f"[-] {f} property flags removed from {target}'s userAccountControl")
