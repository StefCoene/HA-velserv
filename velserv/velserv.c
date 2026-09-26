/* This software is free to use, but if you 'll intend to use it to make money out of it or use parts
notify me @ jeroen dot de dot schepper at skynet dot be
Also if you publish parts or the whole sotfware on your site, be so kind to inform meand link it back to me
Thank you */

#include <sys/types.h>
#include <sys/stat.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <errno.h>
#include <unistd.h>
#include <syslog.h>
#include <string.h>
#include <termios.h>
#include <getopt.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <pthread.h>

#define BAUDRATE B38400
#define MODEMDEVICE "/dev/ttyACM0"
#define IP "127.0.0.1"
#define _POSIX_SOURCE 1         //POSIX compliant source
#define FALSE 0
#define TRUE 1
#define VERSION "1.6"
/* Frames a client may refuse in a row before it is dropped. A healthy client
   drains its buffer immediately, so refusing this many means it is gone. */
#define STALL_LIMIT 20

char *devicename = MODEMDEVICE;
char *IP_ADDRESS = IP;
unsigned int PORT = 3788;
int server_on = 1;
int client_on = 1;

/* Set from the SIGTERM handler, read from main()'s loop. sig_atomic_t so the
   read/write is safe to do outside a signal, and volatile so the compiler
   doesn't cache it in a register across sleep(). We only flip a flag here --
   anything more (closing fds, freeing memory, ...) is not safe to do from
   inside a signal handler and is left to main() once it notices the flag. */
static volatile sig_atomic_t keep_running = 1;

pthread_mutex_t mutex1 = PTHREAD_MUTEX_INITIALIZER;
int fd, sock;
int foreground=0,verbose=0;

/*	Routine to display the data a bit more nicely formatted
*/
int disp_data(unsigned char*lpData, int nSize)
{
	int k;
	char message[8];
	
	fprintf(stdout,"add : %02X | ",lpData[2]);

	if (lpData[1] == 0xf8)
	{
		fprintf(stdout,"prio : high | ");
	}
	if (lpData[1] == 0xfb)
	{
		fprintf(stdout,"prio : low  | ");
	}
	if ((lpData[3]&0xf0)==0x0)
	{
		fprintf(stdout,"rtr : off | ");
	}
	if ((lpData[3]&0xf0)==0x40)
	{
		fprintf(stdout,"rtr : on  | ");
	}
		
	printf("data : ");
	for (k=4; k<(nSize-2); k++)
		{
			sprintf(message,"%02X",lpData[k]);
			fprintf(stdout,"%s ",message);
		} 
		fprintf(stdout,"\n\r");
	return 0;
}



/*	Routine to display date in bulk but hex formatted
*/
int disp_data_full(unsigned char*lpData, int nSize)
{
	int k;
	char message[8];
	
	for (k=0; k<nSize; k++)
		{
			sprintf(message,"%02X",lpData[k]);
			fprintf(stdout,"%s ",message);
		} 
		fprintf(stdout,"\n\r");
	return 0;
}


/* Open the bridge socket: the connection over which the serial port reaches
   the other clients. In the default mode that is velserv connecting to its own
   listening port, so the interface is just another client in the star. */
static int bridge_connect(void)
{
	struct hostent *host;
	struct sockaddr_in server_addr;
	int fd_new;

	host = gethostbyname(IP_ADDRESS);
	if (host == NULL)
		return -1;

	if ((fd_new = socket(AF_INET, SOCK_STREAM, 0)) == -1)
		return -1;

	server_addr.sin_family = AF_INET;
	server_addr.sin_port = htons(PORT);
	server_addr.sin_addr = *((struct in_addr *)host->h_addr);
	bzero(&(server_addr.sin_zero), 8);

	if (connect(fd_new, (struct sockaddr *)&server_addr, sizeof(struct sockaddr)) == -1)
	{
		close(fd_new);
		return -1;
	}
	return fd_new;
}

/* Rebuild the bridge socket after it broke.

   Neither thread used to look at what recv() and send() returned, so a bridge
   that went away left velserv listening and accepting clients while nothing
   reached the bus any more -- connecting still worked, but no traffic passed,
   and only restarting velserv brought it back. Keep trying: the listening
   socket lives in the same process, so the far end is coming back.

   fd_seen is the socket the caller was using. Both threads share one bridge,
   so they both notice the same break; without this check the second one would
   close the healthy socket the first had just made, and the two would keep
   replacing each other's connection until velserv ran out of file descriptors
   and stopped accepting clients altogether. */
static void bridge_reconnect(int fd_seen, const char *why)
{
	int fd_new;

	pthread_mutex_lock(&mutex1);
	if (sock != fd_seen)
	{
		/* Another thread already rebuilt it; carry on with what it made. */
		pthread_mutex_unlock(&mutex1);
		return;
	}
	if (verbose > 3)
		fprintf(stderr, "Velserv: bridge socket lost (%s), reconnecting\n", why);
	close(sock);
	while ((fd_new = bridge_connect()) == -1)
	{
		pthread_mutex_unlock(&mutex1);
		sleep(1);
		pthread_mutex_lock(&mutex1);
	}
	sock = fd_new;
	if (verbose > 3)
		fprintf(stdout, "Velserv: bridge socket reconnected\n");
	pthread_mutex_unlock(&mutex1);
}

void *sock_to_com(void *arg)
{
	(void)arg;
   	unsigned char buffer[100];
   	unsigned char recv_data[1];
   	int start_recv = 0, valid = 0, m = 0, bytes_in_string = 0, bytes_recieved, fd_used;
   	char message[8];
   
	while(1)
	{
	   memset(&recv_data, 0, sizeof(recv_data)); // clear data buffer

	   // wait for data to receive from socket (blocking option)
	   fd_used = sock;
	   bytes_recieved=recv(fd_used,recv_data,1,0);

	   if (bytes_recieved <= 0)
	   {
		   /* A signal is not a broken connection; anything else is. */
		   if (bytes_recieved < 0 && (errno == EINTR || errno == EAGAIN))
			   continue;
		   bridge_reconnect(fd_used, "no data from the bridge");
		   start_recv = 0;
		   valid = 0;
		   m = 0;
		   bytes_in_string = 0;
		   continue;
	   }

	   if (bytes_recieved > 0)
	   {  // if we got some data
			if(verbose == 7)
			{
				sprintf(message,"%02X",recv_data[0]);
				fprintf(stdout,"%s ",message);
			}	
		
			if ((start_recv == 0) && (recv_data[0] == 0xF))
			{
				start_recv = 1;							//start receiving
				memset(&buffer, 0, sizeof(buffer));	//clear receive buffer
				bytes_in_string = 0;
				m = 0;
				valid = 0;
			}
				
			if (start_recv == 1)
			{
				buffer[m] = recv_data[0];
				if (m == 3)
				{
					bytes_in_string = (buffer[3] & 0xF) + 5;
				}
					
				if ((bytes_in_string > 0) && (m == bytes_in_string))
				{
					if (buffer[m] == 0x4)
					{
						start_recv = 0;
						valid = 1;
					}
					else
					{
						start_recv = 0;
						valid = 0;
						if ((verbose > 1) && (verbose <= 3))
						{
							fprintf(stdout,"invalid data from socket\n");
						}
					}
				}
				
				m = m + 1;
			}
		}
		
		if (valid == 1)
		{
			// do some display to debug
			if (verbose==2)
			{
				fprintf(stdout,"COM <- PC: ");
				disp_data_full(buffer,(bytes_in_string+1));
			}
			if (verbose==3)
			{
				fprintf(stdout,"COM <- PC: ");
				disp_data(buffer,(bytes_in_string+1));
			}
			// write the data to the interface and wait for 60000us
			write(fd,buffer,(bytes_in_string+1));
			/* valid is an event ("a frame just completed"), not a state. It has
			   to be consumed here: the only other assignment back to 0 sits in
			   the "a new frame starts" branch, which needs an incoming 0x0F.
			   Without this reset, every byte that is not a frame start falls
			   through both if-blocks unchanged and re-sends this same buffer,
			   turning one frame into an endless stream of identical copies. */
			valid = 0;
			usleep(60000);
		}	


	}
}

void *com_to_sock(void *arg)
{
	(void)arg;
   	unsigned char buffer[100];
   	unsigned char recv_data[1];
   	int start_recv = 0, valid = 0, m = 0, bytes_in_string = 0, bytes_recieved, fd_used;
   	char message[8];
   
	while(1)
	{
	   memset(&recv_data, 0, sizeof(recv_data)); // clear data buffer

	   // wait for data to receive from comport (blocking option)
	   bytes_recieved = read(fd,recv_data,1);
	
	   if (bytes_recieved > 0)
	   {  // if we got some data
			if(verbose == 8)
			{
				sprintf(message,"%02X",recv_data[0]);
				fprintf(stdout,"%s ",message);
			}
		
			if ((start_recv == 0) && (recv_data[0] == 0xF))
			{
				start_recv = 1;							//start receiving
				memset(&buffer, 0, sizeof(buffer));	//clear receive buffer
				bytes_in_string = 0;
				m = 0;
				valid = 0;
			}
				
			if (start_recv == 1)
			{
				buffer[m] = recv_data[0];
				if (m == 3)
				{
					bytes_in_string = (buffer[3] & 0xF) + 5;
				}
					
				if ((bytes_in_string > 0) && (m == bytes_in_string))
				{
					if (buffer[m] == 0x4)
					{
						start_recv = 0;
						valid = 1;
					}
					else
					{
						start_recv = 0;
						valid = 0;
						if ((verbose > 1) && (verbose <= 3))
						{
							fprintf(stdout,"invalid data from com\n");
						}
					}
				}
				
				m = m + 1;
			}
		}
		
		if (valid == 1)
		{
			// do some display to debug
			if (verbose==2)
			{
				fprintf(stdout,"COM -> PC: ");
				disp_data_full(buffer,(bytes_in_string+1));
			}
			if (verbose==3)
			{
				fprintf(stdout,"COM -> PC: ");
				disp_data(buffer,(bytes_in_string+1));
			}
			// write the data to the interface and wait for 60000us
			fd_used = sock;
			if (send(fd_used,buffer,(bytes_in_string+1), 0) == -1)
			{
				bridge_reconnect(fd_used, "cannot send to the bridge");
			}
			valid = 0;   /* consume the event, see the note in sock_to_com() */
			//usleep(10000);
		}	


	}
}

void *server(void *arg)
{
	(void)arg;
	/* master file descriptor list */
	fd_set master;

	/* temp file descriptor list for select() */
	fd_set read_fds;

	/* server address */
	struct sockaddr_in serveraddr2;
 
	/* client address */
	struct sockaddr_in clientaddr;

	/* maximum file descriptor number */
	int fdmax;

	/* listening socket descriptor */
	int listener;

	/* newly accept()ed socket descriptor */
	int newfd;

	/* buffer for& client data */
	unsigned char buf[2048];
	int nbytes;

	/* for setsockopt() SO_REUSEADDR, below */
	int yes = 1;
	socklen_t addrlen;
	int i, j;
	char *ip_add_arr[100] = {0};

	int pointer,bytes_in_string;
	//unsigned char message[8];
	unsigned char buffer[100];

	/* Reassembly of partial Velbus frames across recv() boundaries.
	   TCP is a byte stream without message boundaries, so a single recv()
	   may hold a partial frame or several frames. For every client we keep
	   the trailing bytes that did not yet form a complete frame (a frame is
	   at most 14 bytes) and prepend them to the next recv() for that client. */
	unsigned char work[sizeof(buf) + 16];
	static unsigned char frag[FD_SETSIZE][16];
	static int fraglen[FD_SETSIZE];
	/* consecutive sends refused because a client's buffer is full */
	static int stalled[FD_SETSIZE];

	/* clear the master and temp sets */
	FD_ZERO(&master);
	FD_ZERO(&read_fds);

	/* get the listener */
	if((listener = socket(AF_INET, SOCK_STREAM, 0)) == -1)
	{
		if (verbose >3)
		{
			fprintf(stderr,"Velserv: socket error\n");
		}
		exit(EXIT_FAILURE);
	}

	if (verbose>3)
	{
		fprintf(stdout,"Velserv: socket is OK...\n");
	}
	
	/*"address already in use" error message */
	if(setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(int)) == -1)
	{
		if(verbose>3)
		{
			fprintf(stderr,"Velserv: can't set socket options\n");
			exit(EXIT_FAILURE);
		}
	}

	if (verbose>3)
	{
		fprintf(stdout,"Velserv: setting socket options is OK ...\n");
	}
	
	/* bind */
	serveraddr2.sin_family = AF_INET;
	serveraddr2.sin_addr.s_addr = INADDR_ANY;
	serveraddr2.sin_port = htons(PORT);

	memset(&(serveraddr2.sin_zero), '\0', 8);

 

	if(bind(listener, (struct sockaddr *)&serveraddr2, sizeof(serveraddr2)) == -1)
	{
		if (verbose>3)
		{
			fprintf(stderr,"Velserv: error binding the server!\n");
		}
		exit(EXIT_FAILURE);
	}

	if (verbose>3)
	{
		fprintf(stdout,"Velserv: bind is OK ...\n");
	}

	/* listen */
	if(listen(listener, 10) == -1)
	{
		if (verbose>3)
		{
			fprintf(stderr,"Velserv: error when listening!\n");
		}
		exit(EXIT_FAILURE);
	}

	if (verbose>3)
	{
		fprintf(stdout,"Velserv: server listen is OK ...\n");
	}
	
	/* add the listener to the master set */
	FD_SET(listener, &master);

	/* keep track of the biggest file descriptor */
	fdmax = listener; /* so far, it's this one*/

	/* loop */

	for(;;)
	{
		/* copy it */
		read_fds = master;

		if(select(fdmax+1, &read_fds, NULL, NULL, NULL) == -1)
		{
			if (verbose>3)
			{
				fprintf(stderr,"Velserv: error in select!");
			}
			exit(EXIT_FAILURE);
		}

		/*run through the existing connections looking for data to be read*/
		for(i = 0; i <= fdmax; i++)
		{
			if(FD_ISSET(i, &read_fds))
			{ /* we got one... */
				if(i == listener)
				{
					/* handle new connections */
					addrlen = sizeof(clientaddr);

					if((newfd = accept(listener, (struct sockaddr *)&clientaddr, &addrlen)) == -1)
					{
						if (verbose>3)
						{
							fprintf(stderr,"Velserv: error accepting connection!\n");
						}
					}
					else
					{
						if (verbose>3)
						{
							fprintf(stdout,"Velserv: accepting connection\n");
						}
						if (newfd >= (int)(sizeof(ip_add_arr) / sizeof(ip_add_arr[0])))
						{
							if (verbose>3)
							{
								fprintf(stderr,"Velserv: too many connections, rejecting socket %d\n", newfd);
							}
							close(newfd);
							continue;
						}
						FD_SET(newfd, &master); /* add to master set */

						if(newfd > fdmax)
						{ /* keep track of the maximum */
							fdmax = newfd;
						}

						if (verbose>3)
						{
							fprintf(stdout,"Velserv: new connection from %s on socket %d\n", inet_ntoa(clientaddr.sin_addr), newfd);
						}
						char *ip_add = inet_ntoa(clientaddr.sin_addr);
						ip_add_arr[newfd]=malloc(strlen(ip_add)+1);
						strcpy(ip_add_arr[newfd],ip_add);
						if (newfd < FD_SETSIZE)
						{
							fraglen[newfd] = 0;   /* start with an empty reassembly buffer */
							stalled[newfd] = 0;
						}
					}
				}
				else
				{
					/* handle data from a client */
					memset(&buf, 0, sizeof(buf));
					if((nbytes = recv(i, buf, sizeof(buf), 0)) <= 0)
					{
						/* got error or connection closed by client */
						if(nbytes == 0)
							/* connection closed */
						{
							if (verbose>3)
							{
								fprintf(stdout,"Velserv: %s on socket %d hung up\n",ip_add_arr[i], i);
							}
						}
						else
						{
							if (verbose>3)
							{
								fprintf(stderr,"Velserv: recveive error!\n");
							}
						}
						/* close it... */
						close(i);
						free(ip_add_arr[i]);
						ip_add_arr[i] = NULL;
						/* remove from master set */
						FD_CLR(i, &master);
						if (i < FD_SETSIZE)
						{
							fraglen[i] = 0;   /* drop any half-received frame */
							stalled[i] = 0;
						}
					}
					else if (i < FD_SETSIZE)
					{
						/* We got some data from a client. Prepend the partial
						   frame we stored for this client last time, then forward
						   only complete frames and keep any trailing partial frame
						   for the next recv(). This carries a frame that arrives
						   split over several recv() calls, instead of padding the
						   missing bytes with zeros (the old behaviour that produced
						   corrupt, unparseable messages downstream). */
						int flen = fraglen[i];
						if (flen < 0 || flen > (int)sizeof(frag[0]))
							flen = 0;   /* safety: a stored remainder never exceeds one frame */
						memcpy(work, frag[i], flen);
						memcpy(work + flen, buf, nbytes);
						int worklen = flen + nbytes;

						if(verbose == 9)
						{
							disp_data_full(work, worklen);
						}

						pointer = 0;
						while (worklen - pointer >= 6)   /* 6 = smallest possible frame */
						{
							/* resync: skip anything before a start byte */
							if (work[pointer] != 0x0F)
							{
								pointer++;
								continue;
							}

							bytes_in_string = (work[pointer+3] & 0x0F) + 6;

							/* whole frame not received yet -> wait for the next recv() */
							if (worklen - pointer < bytes_in_string)
							{
								break;
							}

							if (work[pointer+bytes_in_string-1] == 0x04)
							{
								/* a complete, well-framed message -> forward it */
								memcpy(buffer, work + pointer, bytes_in_string);

								for(j = 0; j <= fdmax; j++)
								{
									/* send to everyone! */
									if(FD_ISSET(j, &master))
									{
										/* except the listener and ourselves */
										if(j != listener && j != i)
										{
											/* MSG_DONTWAIT, never a blocking send. A client that stops reading --
											   a laptop asleep behind a vpn leaves the connection half open, with
											   no fin and no rst -- fills its send buffer, and a blocking send()
											   here would stall this thread. It is the only one, so the whole star
											   would stop: no frames for anyone and no accept() either, while the
											   process looks perfectly alive. */
											if(send(j, buffer, bytes_in_string, MSG_DONTWAIT) == -1)
											{
												if (verbose>3)
												{
													fprintf(stderr,"Velserv: error sending!\n");
												}
												if ((errno == EAGAIN || errno == EWOULDBLOCK)
												    && j < FD_SETSIZE && ++stalled[j] >= STALL_LIMIT)
												{
													/* Wedged for many frames in a row, not a passing
													   burst. Drop it; it can always connect again. */
													if (verbose>3)
													{
														fprintf(stdout,"Velserv: dropping unresponsive client on socket %d\n", j);
													}
													close(j);
													FD_CLR(j, &master);
													fraglen[j] = 0;
													stalled[j] = 0;
												}
											}
											else if (j < FD_SETSIZE)
											{
												stalled[j] = 0;
											}
										}
									}
								}
								if (verbose==5)
								{
									fprintf(stdout,"%15s on socket %02i: ",ip_add_arr[i],i);
									disp_data_full(buffer,bytes_in_string);
								}
								if (verbose==6)
								{
									fprintf(stdout,"%15s on socket %02i: ",ip_add_arr[i],i);
									disp_data(buffer,bytes_in_string);
								}
								pointer += bytes_in_string;
							}
							else
							{
								/* start byte but wrong end byte -> resync one byte */
								if (verbose==5)
								{
									fprintf(stderr,"Velserv: framing error \n");
								}
								pointer++;
							}
						}

						/* keep the trailing partial frame (if any) for next time */
						{
							int rem = worklen - pointer;
							/* drop leading garbage so the remainder starts on a frame */
							while (rem > 0 && work[pointer] != 0x0F)
							{
								pointer++;
								rem--;
							}
							if (rem > (int)sizeof(frag[0]))
								rem = 0;   /* cannot happen: an incomplete frame is < 14 bytes */
							memcpy(frag[i], work + pointer, rem);
							fraglen[i] = rem;
						}
					}
				}
			}
		}
	}
	for (i = 0; i < (int)(sizeof(ip_add_arr) / sizeof(ip_add_arr[0])); i++)
	{
		free(ip_add_arr[i]);
	}
}


/* SIGTERM handler: just note that a shutdown was requested. Kept to the bare
   minimum on purpose, since only async-signal-safe calls are allowed here. */
static void handle_sigterm(int sig)
{
	keep_running = 0;
}

static void version (FILE *f, int i)
{
    fprintf(f, "Velserv %s - create a link between the Velbus interface and the network\n", VERSION);
	fprintf(f, "Copyright (C) 2010  Jeroen De Schepper\n");
    exit (i);
}

static void usage (FILE *f, int i)
{
    fprintf(f,"Velserv acts as a gateway between the Velbus interface and the network\n");
	fprintf(f,"Usage: velserv [-csfvhV] [-d DEVICE] [-a ADDRESS] [-p PORT]\n");
	fprintf(f,"-s --server             act as server only, gateway will be disabled\n");
	fprintf(f,"                            when in server mode, the address is always 127.0.0.1\n");
	fprintf(f,"-c --client             act as client only, server wil be disabled\n");
	fprintf(f,"-d --device INTERFACE   device where the Velbus interface is connected to\n");
	fprintf(f,"                            default device is: /dev/ttyACM0\n");
	fprintf(f,"-a --address HOST       IP address or hostname where to connect to as client\n");
	fprintf(f,"                            default is 127.0.0.1\n");
	fprintf(f,"-p --port PORT          port where to connect\n");
	fprintf(f,"                            default is 3788\n");
	fprintf(f,"-f --foreground         do not run in background\n");
	fprintf(f,"-v --verbose            verbose operation, repeat for debugging output\n");
	fprintf(f,"                            1 general debug, 2-3 com to socket debug, 4-6 server socket debug\n");
	fprintf(f,"-h --help               print this help and exit\n");
	fprintf(f,"-V --version            print version information and exit\n");
    exit (i);
}


static struct option long_options[] = {
  { "server", no_argument, NULL, 's' },
  { "client", no_argument, NULL, 'c' },
  { "device", required_argument, NULL, 'd' },
  { "address", required_argument, NULL, 'a'},
  { "port", required_argument, NULL, 'p' },
  { "foreground", no_argument, NULL, 'f' },
  { "verbose", no_argument, NULL, 'v' },
  { "help", no_argument, NULL, 'h' },
  { "version", no_argument, NULL, 'V'},
  { NULL, 0, NULL, 0 } };
  
  
/* Parse command line parameters */
static void parse_params(int argc, char **argv)
{
    while(1) {
	int c = getopt_long (argc, argv, "a:p:d:fvschV", long_options, NULL);
	if (c == -1)
	    break;
	switch(c) {
		case 'a':
			IP_ADDRESS = strdup (optarg);
			break;
		case 'p':
			{
				char *endptr = NULL;
				long parsed_port = strtol(optarg, &endptr, 10);
				if (*optarg == '\0' || *endptr != '\0' || parsed_port < 1 || parsed_port > 65535)
				{
					fprintf(stderr, "Velserv: invalid port '%s'\n", optarg);
					exit(EXIT_FAILURE);
				}
				PORT = (unsigned int)parsed_port;
			}
			break;
	    case 'd':
			devicename = strdup (optarg);
			break;
		case 's':
			server_on = 1;
			client_on = 0;
			break;
		case 'c':
			server_on = 0;
			client_on = 1;
			break;
	    case 'f':
			foreground = 1;
			break;
	    case 'h':
			usage (stdout, 0);
			break;
		case 'v':
			verbose++;
			foreground = 1;
			break;
	    case 'V':
			version (stdout, 0);
	    	break;
		case '?':
			usage (stdout, 0);
			break;
		}
	}
}


int main (int argc, char **argv)
{

   	char com_mess[100];

   	struct termios oldtio, newtio;       //place for old and new port settings for serial port

   	pthread_t thread1, thread2, thread3;
   	int iret1, iret2, iret3;
   	int RTS_flag;
   	int DTR_flag;

   	/*###################################################################################################
      #		read the parameters from the command line (and display them)							   #
	  ###################################################################################################
   	*/
   
   	parse_params (argc, argv);
	
    if (optind > argc) 
	{
		usage (stderr, 1);
    }
	
	if(server_on)
	{
		IP_ADDRESS = IP;
	}


	if (verbose > 0)
	{
		printf(   "Device        : %s\n",devicename);
		printf(   "IP address    : %s\n",IP_ADDRESS);
		printf(   "Port          : %i\n",PORT);
		printf(   "Verbose level : %i\n",verbose);
		printf(   "Client mode   : %i\n",client_on);
		printf(   "Server mode   : %i\n",server_on); //output the received setup parameters
	}

	signal(SIGIO, SIG_IGN);
	signal(SIGPIPE, SIG_IGN);
	signal(SIGTERM, handle_sigterm);

  	/*################################################################################################
      #		command to configure comports, wierd but needed to use the velbus interface			   #
	  ################################################################################################
  	*/
	if(client_on)
	{
		sprintf(com_mess,"/bin/stty -F %s 9600 crtscts",devicename);
		if((system(com_mess))<0){
			fprintf(stderr,"Velserv: cannot set port (stty)\n"); // 
			exit(EXIT_FAILURE);
		}
	}
		
		
    /* Our process ID and Session ID */
    pid_t pid, sid;
    if (!foreground) 
	{
        /* Fork off the parent process */
        pid = fork();
        if (pid < 0) 
		{
			exit(EXIT_FAILURE);
        }
        /* If we got a good PID, then
           we can exit the parent process. */
        if (pid > 0) 
		{
			exit(EXIT_SUCCESS);
        }

        /* Change the file mode mask */
        umask(0);
                        
        /* Create a new SID for the child process */
        sid = setsid();
        if (sid < 0) 
		{
			/* Log the failure */
            exit(EXIT_FAILURE);
        }
        
        /* Change the current working directory */
        if ((chdir("/")) < 0) 
		{
			/* Log the failure */
            exit(EXIT_FAILURE);
        }
        
        /* Close out the standard file descriptors */
        close(STDIN_FILENO);
        close(STDOUT_FILENO);
        close(STDERR_FILENO);
	}
    	
	if (server_on)
	{
		iret3 = pthread_create(&thread3, NULL, server, NULL);
		if (iret3 != 0)
		{
			fprintf(stderr, "Velserv: cannot start server thread\n");
			exit(EXIT_FAILURE);
		}
	}
		
	/*##################################################################################################
	  #			comport configuration and activation												   #
	  ##################################################################################################
	*/
	if(client_on)
	{
		sleep(1);
		fd = open(devicename, O_RDWR | O_NOCTTY );
		if (fd < 0)
		{
			perror(devicename);
			exit(EXIT_FAILURE);
		}
		fcntl(fd, F_SETFL, FASYNC);
		memset(&oldtio, 0, sizeof(struct termios));
		
		if((tcgetattr(fd,&oldtio))<0)
		{
			fprintf(stderr,"Velserv: cannot read old settings\n"); // save current port settings 
			exit(EXIT_FAILURE);
		}

		if(cfsetospeed(&newtio, BAUDRATE) < 0 || cfsetispeed(&newtio, BAUDRATE) < 0 || tcsetattr(fd,TCSAFLUSH,&newtio) < 0)
		{
			fprintf(stderr,"Velserv: cannot set baud rates\n");
			exit(EXIT_FAILURE);
		}
		
		memset(&newtio, 0, sizeof(struct termios));
	
		if ((tcgetattr(fd,&newtio)) < 0)
		{
			fprintf(stderr,"Velserv: cannot read new attribs\n");
			exit(EXIT_FAILURE);
		}

		newtio.c_iflag &= ~(BRKINT | ICRNL | IGNCR | INLCR | INPCK | ISTRIP | IXON | IXOFF | PARMRK);
		newtio.c_iflag |= IGNBRK | IGNPAR;
		newtio.c_lflag &= ~(ECHO | ECHOE | ECHOK | ECHONL | ICANON | IEXTEN | ISIG);
		newtio.c_oflag &= ~(OPOST);
		newtio.c_cflag &= ~(CSIZE | HUPCL | PARENB);
		newtio.c_cflag |= (CLOCAL | CS8 | CREAD | CRTSCTS);
	
		newtio.c_cc[VMIN] = 1;
		newtio.c_cc[VTIME] = 1;
		cfmakeraw(&newtio);
		
		if((tcsetattr(fd, TCSAFLUSH, &newtio)) < 0)
		{
			fprintf(stderr,"Velserv: cannot set new attribs\n");
			exit(EXIT_FAILURE);
		}
	
		tcflush(fd, TCIOFLUSH);
		fflush(NULL);
		
		RTS_flag = TIOCM_RTS;
		DTR_flag = TIOCM_DTR;
		
		ioctl(fd,TIOCMBIS,&RTS_flag);//Set RTS pin
		ioctl(fd,TIOCMBIC,&DTR_flag);//Set DTR pin

		/*##################################################################################################
		#			socket configuration and activation												       #
		##################################################################################################
		*/

		if ((sock = bridge_connect()) == -1)
		{
			fprintf(stderr,"Velserv: error connecting client to server\n");
			exit(EXIT_FAILURE);
		}


		iret1 = pthread_create(&thread1, NULL, sock_to_com, NULL);
		if (iret1 != 0)
		{
			fprintf(stderr, "Velserv: cannot start socket-to-serial thread\n");
			exit(EXIT_FAILURE);
		}

		iret2 = pthread_create(&thread2, NULL, com_to_sock, NULL);
		if (iret2 != 0)
		{
			fprintf(stderr, "Velserv: cannot start serial-to-socket thread\n");
			exit(EXIT_FAILURE);
		}
	}
	
    while (keep_running) 
	{
		/* Do some task here ... */
        sleep(1); /* check keep_running often enough for a prompt shutdown */
    }

	if (verbose > 0)
	{
		fprintf(stdout, "Velserv: SIGTERM received, shutting down\n");
	}

	//end of program
	/* thread1/2/3 sit in blocking calls (recv/read/select) with no way to
	   notice keep_running, so ask them to stop instead of waiting for them
	   to come back on their own. pthread_join right after is then safe: it
	   waits for each thread to actually unwind rather than for it to exit
	   on its own, which for these loops would be never. */
	if(server_on)
	{
		pthread_cancel(thread3);
		pthread_join( thread3, NULL);
	}
		
	if(client_on)
	{
		pthread_cancel(thread1);
		pthread_cancel(thread2);
		pthread_join( thread1, NULL);
		pthread_join( thread2, NULL);
		tcsetattr(fd,TCSANOW,&oldtio); //set back ols comport settings
		close(fd);        //close the com port
		close(sock);		//close the socket
	}
	
	exit(EXIT_SUCCESS);
}
