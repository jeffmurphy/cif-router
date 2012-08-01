#!/usr/bin/python
#
# cif-router proof of concept
#
# cif-router [-p pubport] [-r routerport] [-m myname] [-h] 
#      -p  default: 5556
#      -r  default: 5555
#      -m  default: cif-router
#
# cif-router is a zmq device with the following sockets:
#     XPUB 
#       for republishing messages 
#     XSUB
#       for subscribing to message feeds
#     ROUTER
#       for routing REQ/REP messages between clients
#       also for accepting REQs from clients
#         locally accepted types:
#            REGISTER, UNREGISTER, LIST-CLIENTS
#         locally generated replies:
#            UNAUTHORIZED, OK, FAILED
#
# communication between router and clients is via CIF.msg passing
# the 'ControlStruct' portion of CIF.msg is used for communication
#
# a typical use case:
# 
# cif-smrt's REQ connects to ROUTER and sends a REGISTER message with dst=cif-router
# cif-router's ROUTER responds with SUCCESS (if valid) or UNAUTHORIZED (if not valid)
#     the apikey will be validated during this step
# cif-router's XSUB connects to cif-smrt's XPUB
# cif-smrt begins publishing CIF messages 
# cif-router re-publishes the CIF messages to clients connected to cif-router's XPUB 
#    clients may be: cif-correlator, cif-db

import sys
import zmq
import time
import datetime
import threading
import getopt
import json

#sys.path.append('/usr/local/lib/cif-protocol/pb-python/gen-py')
sys.path.append('/media/psf/Home/git/cif-protocol/src/pb-python/gen-py')
import msg_pb2
import feed_pb2
import RFC5070_IODEF_v1_pb2
import MAEC_v2_pb2
import control_pb2
import cifsupport

myname = "cif-router"

def register(clientname):
    # zmq doesnt have a disconnect, so if we xsub.connect() multiple times
    # to the same client, we'll start recving duplicates of that clients
    # messages. to avoid this, we track who we've connected to and if we
    # see the same client more than once, we dont call connect() again.
    
    #if clientname in clients :
    #    print "\talready registered"
    #    return 'ALREADY-REGISTERED'
    

    clients[clientname] = time.time()
    return control_pb2.ControlType.SUCCESS

def dosubscribe(m):
    if m.src in publishers :
        print "we've seen this client before. re-using old connection."
    else:
        publishers[m.src] = time.time()
        addr = m.iPublishRequest.ipaddress
        port = m.iPublishRequest.port
        print "dosubscribe: connect our xsub -> xpub on " + addr + ":" + str(port)
        xsub.connect("tcp://" + addr + ":" + str(port))
    return control_pb2.ControlType.SUCCESS

def unregister(clientname):
    if clientname in clients :
        print "\tunregistered"
        # see explanation in register()
        #del clients[clientname]
        return control_pb2.ControlType.SUCCESS
    print "\tclient unknown"
    return control_pb2.ControlType.FAILED

def list_clients():
    l = ''
    m = control_pb2.ListClientsResponse()
    for k in clients.keys():
        m.client.extend(k)
        m.connectTimestamp.extend(clients[k])
        #l = l + "%{client}s %{time}d\n" % { 'client' : k, 'time' : clients[k] }
    return m

def myrelay(pubport):
#    zmq.device(zmq.FORWARDER, xpub, xsub)
    relaycount = 0
    print "[myrelay] Create XPUB socket on " + str(pubport)
    xpub = context.socket(zmq.PUB)
    xpub.bind("tcp://*:" + str(pubport))
    while True:
        relaycount = relaycount + 1
        print "[myrelay] " + str(relaycount) + " recv()"
        m = xsub.recv()
        #print "[myrelay] got msg on our xsub socket: " , m
        xpub.send(m)
    
def usage():
    print "cif-router [-r routerport] [-p pubport] [-m myid] [-h]"
    print "   routerport = 5555, pubport = 5556, myid = cif-router"
        
try:
    opts, args = getopt.getopt(sys.argv[1:], 'p:r:m:h')
except getopt.GetoptError, err:
    print str(err)
    usage()
    sys.exit(2)

context = zmq.Context()
clients = {}
publishers = {}
routerport = 5555
publisherport = 5556
myid = "cif-router"

for o, a in opts:
    if o == "-r":
        routerport = a
    elif o == "-p":
        publisherport = a
    elif o == "-m":
        myid = a
    elif o == "-h":
        usage()
        sys.exit(2)
        
print "Create ROUTER socket on " + str(routerport)
socket = context.socket(zmq.ROUTER)
socket.bind("tcp://*:" + str(routerport))
socket.setsockopt(zmq.IDENTITY, myname)

print "Create XSUB socket"
xsub = context.socket(zmq.SUB)
xsub.setsockopt(zmq.SUBSCRIBE, '')

print "Connect XSUB<->XPUB"
thread = threading.Thread(target=myrelay, args=(publisherport,))
thread.start()

print "Entering event loop"

try:
    while True:
        print "Get incoming message"
        rawmsg = socket.recv_multipart()
        #print " Got ", rawmsg
        msg = control_pb2.ControlType()
        
        try:
            msg.ParseFromString(rawmsg[2])
        except Exception as e:
            print "Received message isn't a protobuf: ", e
        else:
            msgreallyfrom = rawmsg[0] # save the ZMQ identity of who sent us this message
            
            #print "Got msg: ", msg
    
            try:
                cifsupport.versionCheck(msg)
            except Exception as e:
                print "Received message has incompatible version: ", e
            else:
            
                if cifsupport.isControl(msg):
                    msgfrom = msg.src
                    msgto = msg.dst
                    msgcommand = msg.command
                    
                    if msgto == myname and msg.type == control_pb2.ControlType.COMMAND:
                        print "COMMAND for me: ", msgcommand
                    
                        if msgcommand == control_pb2.ControlType.REGISTER:
                              print "REGISTER from: " + msgfrom
                              rv = register(msgfrom)
                              msg.status = rv
                              msg.type = control_pb2.ControlType.REPLY
                              if rv == control_pb2.ControlType.SUCCESS:
                                  msg.registerResponse.REQport = routerport
                                  msg.registerResponse.PUBport = publisherport
                                  print " Registered successfully. Sending reply."
                              else:
                                  print " Failed to register. Sending reply."
                              socket.send_multipart([msgreallyfrom, '', msg.SerializeToString(), ''])
                                          
                        elif msgcommand == control_pb2.ControlType.UNREGISTER:
                            print "UNREGISTER from: " + msgfrom
                            rv = unregister(msgfrom)
                            msg.status = rv
                            socket.send_multipart([ msgreallyfrom, '', msg.SerializeToString(), ''])
                        
                        elif msgcommand == control_pb2.ControlType.LISTCLIENTS:
                             print "LIST-CLIENTS for: " + msgfrom
                             rv = list_clients()
                             msg.status = control_pb2.ControlType.SUCCESS
                             msg.listClientsReponse = rv
                             socket.send_multipart( [ msgreallyfrom, '', msg.SerializeToString(), '' ] )
                             
                        elif msgcommand == control_pb2.ControlType.IPUBLISH:
                             print "IPUBLISH from: " + msgfrom
                             rv = dosubscribe(msg)
                             msg.status = rv
                             socket.send_multipart( [msgreallyfrom, '', msg.SerializeToString(), ''] )

except KeyboardInterrupt:
    print "Shut down."
    if thread.isAlive():
        try:
            thread._Thread__stop()
        except:
            print(str(thread.getName()) + ' could not be terminated')
    sys.exit(0)

    
    