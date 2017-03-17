import socketserver

import binascii
import threading

import math

from syhelpers.dataqueue import DataQueue
from syhelpers.encoding import dnshostdecode, dnstxtencode, lenofb64decoded, dnsip4encode, dnshostencode, dnsip6encode
from syhelpers.types import isportnumber, isint
from .transport import Transport
from syhelpers.log import *
from syhelpers.modulebase import ModuleBase
import dns
import dns.message

DEBUG_MODULE = "TransportDns"
MAX_TXT_ENTRY_LEN = 250


class TransportDns (Transport, ModuleBase):
    """ allows and handles DNS query based connections """

    # noinspection PyMissingConstructor
    def __init__(self, handler):
        """
        initializese the module
        :param handler: backreference to syssspy handler object
        """

        self.options = {
            'ZONE' : {
                'Description'   :   'DNS Zone for handling requests',
                'Required'      :   True,
                'Value'         :   None
            },
            'LHOST': {
                'Description'   :   'Interface IP to listen on',
                'Required'      :   True,
                'Value'         :   "0.0.0.0"
            },
            'LPORT' : {
                'Description'   :   'UDP-Port to listen on for DNS server',
                'Required'      :   True,
                'Value'         :   "53"
            },
            'DNSTYPE': {
                'Description'   :   'DNS type to use for the connection',
                'Required'      :   True,
                'Value'         :   "TXT",
                'Options'       :   ("TXT", "A")
            },
            'DNSSERVER': {
                'Description'   :   'IP address of DNS server to connect for all queries',
                'Required'      :   False,
                'Value'         :   None
            },
            'PROGRESSBAR': {
                'Description'   :   'Display a progressbar for uploading the staged agent? ' +
                                    '(only if not debugging this module)',
                'Required'      :   True,
                'Value'         :   "TRUE",
                'Options'       :   ("TRUE", "FALSE")
            }
        }
        self.handler = handler
        self.server = None
        self.staged = False
        self.currentstagenum = 0
        self.senddataqueue = DataQueue()
        self.recvdataqueue = DataQueue()
        self.progress = None
        self.maxstagenum = None
        self.laststagepart = None
    
    def setoption(self, name, value):
        """
        Sets an option
        :param name: name of the option
        :param value: new value
        :return: True iff the value was found, not necessary set!
        """

        # TODO: check interface ip and DNSSERVER

        if name.upper() == "ZONE" and not(self._validate_zone("ZONE", value)):
            return True  # value found, but not set

        if name.upper() == "LPORT" and not(self._validate_port("LPORT", value)):
            return True  # value found, but not set
        elif isint(value) and int(value) != 53:
            print_error("DNS might not work if you set a non-default port. We will assume, " +
                        "you know what you do and continue.")
            # and continue setting it

        return ModuleBase.setoption(self, name, value)

    @staticmethod
    def _validate_zone(name, zone):
        """
        validates whether DNS zone is plausible
        :param name: name of the option field, used for outputs
        :param zone: string of the DNS zone to validate
        :return: True iff we could validate the zone
        """

        # TODO: check zone

        if not zone or not str(zone):
            print_error(str(name)+" is invalid")
            return False
        else:
            return True

    @staticmethod
    def _validate_port(name, port):
        """
        checks whether the port value is plausible
        :param name: name of the option, to use in messages
        :param port: port number to check
        :return: True iff plausible
        """

        if not isportnumber(port):
            print_error(str(name)+" is invalid, should be 1 <= port <= 65535")
            return False
        else:
            return True

    def validate_options(self):
        """
        Validate all currently set listener options.
        """
        
        valid = ModuleBase.validate_options(self)

        # TODO: check interface ip LHOST and DNSSERVER

        port = self.options['LPORT']['Value']
        if port and not(self._validate_port('LPORT', port)):
            valid = False

        zone = self.options['ZONE']['Value']
        if zone and not(self._validate_zone('ZONE', port)):
            valid = False

        return valid
    
    def open(self, staged=False):
        """
        open the DNS server and listen for connections
        :param staged: should we stage first?
        :return: None
        """

        if not self.validate_options():
            return

        self.staged = staged
        self.currentstagenum = 0

        # mark backchannel to us from each DnsHandler instance
        DnsHandler.transport = self

        lparams = (self.options['LHOST']['Value'], int(self.options['LPORT']['Value']))

        self.server = socketserver.UDPServer(lparams, DnsHandler)
        threading.Thread(target=self.server.serve_forever).start()

        print_message("DNS listening on {}:{}".format(*lparams))
   
    def send(self, data):
        """
        send data to the connected host
        :param data: data to send
        :return: None
        """

        if not self.server:
            print_error("Connection not open")
            return

        # add the data to the send queue
        self.senddataqueue.add(data)

        # block until send
        while self.senddataqueue.has_data():
            pass

    def receive(self, leng=1024):
        """
        receive data from connected host
        :param leng: length of data to collect
        :return: data
        """

        if not self.server:
            print_error("Connection not open")
            return

        # if there is no data, block until there is
        while not self.recvdataqueue.has_data():
            pass

        # finish even if less data than requested, higher level must handle this
        return self.recvdataqueue.get(leng)

    def upgradefromstager(self):
        """
        upgrade the connection from staged form to unstaged real connection
        :return: None
        """

        # server stays open, we just accept no staging anymore
        self.staged = False

    def upgradetotls(self):
        """
        upgrade to a tls wrapped connection
        :return: None
        """

        # TODO: implement
        print_error("DNS + TLS is not implemented yet")
        return

        # TODO: newer TLS version?
        #context = ssl.SSLContext(ssl.PROTOCOL_TLSv1)
        # TODO: load the certificate from the correct option path
        #context.load_cert_chain(certfile="./data/syssspy.pem", keyfile="./data/syssspy.pem")
        #self.conn = context.wrap_socket(self.conn, server_side=True)
        #print_message("Upgrade to TLS done")

    def close(self):
        """
        Close the connection
        :return: None
        """

        if not self.server:
            print_error("Connection not open")
            return

        self.server.shutdown()
        self.server.server_close()
        self.server = None

    def serve_stage(self, stagepartnum):
        """
        should serve the next part of the staged agent, if the number matches
        :param stagepartnum: number of the stager part to get
        :return: part of the staged agent or None
        """

        if self.currentstagenum - 1 == stagepartnum and self.laststagepart:
            return self.laststagepart

        if not self.staged:
            print_error("stager request for TransportDns but its not staged, dropping")
            return None

        if self.currentstagenum != stagepartnum:
            print_debug(DEBUG_MODULE, "request for different stager part number, expected: {}, received: {}".format(
                self.currentstagenum, stagepartnum))
            return None  # do not answer more than once

        if not self.senddataqueue.has_data():
            print_debug(DEBUG_MODULE, "out of stager data to send")
            return None  # end of data to send / stager code

        # calculate lenght and maximal stage number
        if self.options['DNSTYPE']['Value'] == "TXT":
            maxlendata = lenofb64decoded(MAX_TXT_ENTRY_LEN)  # TODO: change for non Base64 encoding schemes
        elif self.options['DNSTYPE']['Value'] == "A":
            maxlendata = 4
        else:
            print_error("invalid DNSTYPE")
            return None

        if self.maxstagenum is None:
            self.maxstagenum = math.ceil(self.senddataqueue.length() / maxlendata) - 1

        # create progress bar if selected
        if self.progress is None and self.options['PROGRESSBAR']['Value'] == "TRUE" \
                and isactivated(DEBUG_MODULE):
            import progressbar
            self.progress = progressbar.ProgressBar(0, self.maxstagenum)

        # print progress either in debug line or as progressbar (if selected)
        if isactivated(DEBUG_MODULE):
            print_debug(DEBUG_MODULE, "Sending staged agent part {} of {}".format(self.currentstagenum,
                                                                                  self.maxstagenum))
        elif self.progress is not None:
            self.progress.update(self.currentstagenum)
            if self.currentstagenum == self.maxstagenum:
                print()

        # return next data segment and increase segment counter
        nextdata = self.senddataqueue.get(maxlendata)
        self.currentstagenum += 1
        self.laststagepart = nextdata
        return nextdata

    def serve_ping(self, pingdata):
        """
        should serve a pong response valid for the encoding DNS type
        :param pingdata: b'pingquery' usually
        :return: b'pong' or something alike
        """

        # TODO: remember to match strange encoding for DNS types
        return b'pong'


class DnsHandler(socketserver.BaseRequestHandler):
    """
    This class is instanciated once per connection and should handle the DNS requests
    """

    # the transport object above, that initiated this
    transport = None

    def __init__(self, request, client_address, server):
        """
        initiate a new DNS handler for a request
        """

        self.zone = self.transport.options["ZONE"]["Value"].rstrip(".")
        self.stagerrequest = False
        self.dnstype = None

        print_debug(DEBUG_MODULE, "zone = " + str(self.zone))
        super().__init__(request, client_address, server)

    def _is_in_zone(self, queryname):
        """
        tests whether the domain name queried is part of our zone
        :param queryname: domain name queried
        :return: True iff it is in our zone
        """

        return str(queryname).rstrip(".").endswith(self.zone)

    def _decode_query(self, queryname):
        """
        decodes the query content according to our specification
        :param queryname: domain name queried
        :return: decoded query string or None if decoding failed
        """

        # strip zone and remove all dots
        q = str(queryname).rstrip(".").rstrip(self.zone).replace(".", "")

        # remove random part (used to avoid DNS caching issues)
        q2, r = q.rsplit("r", maxsplit=1)
        if not r or not isint(r):
            q2, r = q.rsplit("R", maxsplit=1)
            if not r or not isint(r):
                print_error("stripping the random part from DNS query failed, ignoring this query")
                return None
        q = q2

        #print_debug(DEBUG_MODULE, "q = {}, q.startswith('s') = {}, q.strip('s').isdigit() = {}".format(
        #    q, q.startswith('s'), q.strip('s').isdigit()))

        if q.startswith("s") and q.strip("s").isdigit():  # stager request
            self.stagerrequest = True
            return int(q.strip("s"))  # we will not decode it further

        # otherwise we expect a fully enrolled agent on the other side and decode
        try:
            return dnshostdecode(q)
        except binascii.Error:
            return None

    def _encode_response(self, rdata, dnstype):
        """
        encodes the response data to a form we can include in a DNS response of the given type
        :param rdata: data to include
        :param dnstype: type to use for response, e.g. dns.rdatatype.TXT
        :return: encoded data
        """

        if dnstype is dns.rdatatype.TXT:
            return dnstxtencode(rdata)
        elif dnstype is dns.rdatatype.A:
            return dnsip4encode(rdata)
        elif dnstype is dns.rdatatype.CNAME:
            return dnshostencode(rdata, self.zone)
        elif dnstype is dns.rdatatype.MX:
            return b'10 ' + dnshostencode(rdata, self.zone)
        elif dnstype is dns.rdatatype.AAAA:
            return dnsip6encode(rdata)
        else:
            print_error("invalid DNSTYPE for encoding requested: {}".format(dnstype))
            return None

    def _get_response(self, qtext):
        """
        finds the response for the given query text
        :param qtext: query to respond to (already decoded)
        :return: response (not yet encoded)
        """

        if self.stagerrequest:  # stager request
            return self.transport.serve_stage(qtext)

        if qtext == b'pingquery':
            return self.transport.serve_ping(qtext)

        return qtext  # TODO: for now we just reply

    def _dns_type(self):
        """
        Should return the DNS response data type needed, TXT or A for stager or anything we support else
        :return: dns.rdatatype.TXT or dns.rdatatype.A or other
        """

        if self.stagerrequest:
            if self.transport.options['DNSTYPE']['Value'] == "TXT":
                return dns.rdatatype.TXT
            elif self.transport.options['DNSTYPE']['Value'] == "A":
                return dns.rdatatype.A
            else:
                print_error("invalid DNSTYPE")
                return None

        # if not staging, we can be more creative
        else:
            return self.dnstype


    def handle(self):
        """
        handles a single DNS request and sends a response
        :return: None
        """

        data = self.request[0]
        socket = self.request[1]

        try:
            msg = dns.message.from_wire(data)
        except Exception as e:
            print_error("invalid DNS message ({}): {}".format(str(e), data))
            return

        if msg.opcode() != 0:  # not a query
            print_error("invalid DNS request received: "+str(msg))
            return

        for q in msg.question:
            ptrquery = False

            print_debug(DEBUG_MODULE, "query from {}: {}".format(self.client_address[0], str(q)))

            if "IN PTR" in str(q):
                self.dnstype = dns.rdatatype.PTR
                qtext = str(q.name)

            elif not self._is_in_zone(q.name):
                self.dnstype = None
                print_error("ignoring query outsite of our zone: " + str(q))
                continue

            else:
                qtext = self._decode_query(q.name)
                if qtext is None:
                    print_error("decoding failed for query: " + str(q))
                    continue
                print_debug(DEBUG_MODULE, "decoded qtext = {}".format(qtext))

                if "IN TXT" in str(q):
                    self.dnstype = dns.rdatatype.TXT
                elif "IN MX" in str(q):
                    self.dnstype = dns.rdatatype.MX
                elif "IN CNAME" in str(q):
                    self.dnstype = dns.rdatatype.CNAME
                elif "IN AAAA" in str(q):
                    self.dnstype = dns.rdatatype.AAAA
                elif "IN A" in str(q):
                    self.dnstype = dns.rdatatype.A
                else:
                    self.dnstype = None

            resp = dns.message.make_response(msg)
            resp.flags |= dns.flags.AA
            resp.set_rcode(0)
            if resp:
                if self.dnstype is not dns.rdatatype.PTR:
                    data = self._get_response(qtext)
                    if data:
                        dnstype = self._dns_type()
                        data = self._encode_response(data, dnstype)
                        if data:
                            print_debug(DEBUG_MODULE, "responding with: {}".format(str(data, 'utf-8')))
                            resp.answer.append(dns.rrset.from_text(q.name, 7600, dns.rdataclass.IN, dnstype,
                                                               str(data, 'utf-8')))
                            socket.sendto(resp.to_wire(), self.client_address)
                        else:
                            print_debug(DEBUG_MODULE, "no data to respond after encoding, ignoring query")
                    else:
                        print_debug(DEBUG_MODULE, "no data to respond, ignoring query")
                else:
                    data = self.zone + '.'  # absolute name, dot is needed here!
                    print_debug(DEBUG_MODULE, "responding to PTR query with zone: {}".format(data))
                    resp.answer.append(dns.rrset.from_text(q.name, 7600, dns.rdataclass.IN, dns.rdatatype.PTR, data))
                    socket.sendto(resp.to_wire(), self.client_address)
            else:
                print_error("error creating response for DNS query: " + msg)
                return