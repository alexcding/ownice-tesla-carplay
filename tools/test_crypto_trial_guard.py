#!/usr/bin/env python3
"""Compile and fault-test the actual C control flow using a syscall fixture.

This does not validate Linux/ARM syscall behavior on physical hardware.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

SOURCE = Path(__file__).with_name('crypto_trial_guard.c')
HARNESS = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <setjmp.h>
static long call6(int,unsigned long,unsigned long,unsigned long,unsigned long,unsigned long,unsigned long);
#define UNIT_TEST
#include "SOURCE"
static struct shared shared_state;
static char events[8192], texts[128][2048];
static int offsets[128], next_fd=40, forks, tick, old_dead, test_dead, self_dead;
static int stopped, resumed, scenario, null_opened, interrupt_given, pipe_ignored, mask_restored;
static jmp_buf exit_jump;
static int child_status;
static void event(const char *s) { strcat(events,s); strcat(events,"\n"); }
static long call6(int n,word a,word b,word c,word d,word e,word f) {
 (void)d; (void)e; (void)f;
 switch(n) {
 case 1: child_status=(int)a; longjmp(exit_jump,1);
 case 2:
  forks++;
  if(forks==1) return scenario==10?-1:300;
  if(scenario==11) return -1;
  if(scenario==12||scenario==13||scenario==14||scenario==28) return 0;
  return 400;
 case 3:
  if(a==30) { *(char*)b='R'; return scenario==9?0:1; }
  { size_t size=strlen(texts[a])+1;
    if(strstr(texts[a],"LD_LIBRARY_PATH")) size=strlen(texts[a])+1;
    if(offsets[a]>=(int)size) return 0;
    size_t take=size-offsets[a]; if(take>c) take=c;
    memcpy((void*)b,texts[a]+offsets[a],take); offsets[a]+=(int)take; return (long)take; }
 case 4:
  if(a==1) { strncat(events,(const char*)b,c); return (long)c; }
  return (long)c;
 case 5: {
  const char *name=(const char*)a;
  if(!strcmp(name,"/dev/null")) { null_opened=1; return 80; }
  int fd=next_fd++;
  if(strstr(name,"/stat")&&!strstr(name,"/status")) {
   int parent=strstr(name,"/200/")?100:1;
   sprintf(texts[fd],"200 (receiver ) name) S %d",parent);
   for(int i=0;i<17;i++) strcat(texts[fd]," 0");
   strcat(texts[fd]," 123\n");
  } else if(strstr(name,"/cmdline")) strcpy(texts[fd],"z-tesla");
  else if(strstr(name,"/environ")) {
   if(scenario==2) return -1;
   strcpy(texts[fd],"LD_LIBRARY_PATH=/usr/z-loader/lib");
  } else if(strstr(name,"/status")) {
   if(scenario==26) strcpy(texts[fd],"SigIgn:\tmalformed\nSigBlk:\t0000000000000000\n");
   else if(scenario==27) strcpy(texts[fd],"SigIgn:\t0000000000001006\n");
   else strcpy(texts[fd],"SigIgn:\t0000000000001006\nSigBlk:\t0000000100000000\n");
  } else return -1;
  return fd; }
 case 6: return 0;
 case 11:
  event("exec");
  if(!null_opened||!equal(cwd,"/usr/z-loader")||!envp[0]) abort();
  return -1; /* simulated exec failure */
 case 12: return scenario==14?-1:0;
 case 20: return 250;
 case 37:
  if(a!=400||b!=9) abort(); event("kill-own-child"); test_dead=1; return 0;
 case 42: ((int*)a)[0]=30; ((int*)a)[1]=31; return 0;
 case 55: return 0;
 case 63: return (long)b;
 case 64: return scenario==13?999:250;
 case 66: return 350;
 case 85: memcpy((void*)b,"/usr/z-loader",13); return 13;
 case 114: *(int*)b=a==400?(scenario==4?11:scenario==18?126<<8:0):0; return (long)a;
 case 162:
  if(scenario==16&&stopped&&!interrupt_given++) return -4;
  if(scenario==17&&stopped) return -5;
  return 0;
 case 168: {
  struct pollfd *p=(struct pollfd*)a;
  if(p->fd==11) p->revents=old_dead;
  else if(p->fd==12) p->revents=self_dead?(scenario==24?0x11:1):0;
  else if(p->fd==13) p->revents=scenario==3;
  else if(p->fd==14) p->revents=scenario==19?0x20:test_dead||scenario==4||scenario==18;
  else p->revents=0;
  return p->revents?1:0; }
 case 172: return scenario==12?-1:0;
 case 174:
  if(a==13&&*(word*)b==1) { pipe_ignored=1; if(scenario==28) return -22; }
  return 0;
 case 175:
  if(a!=2||((u32*)b)[0]!=0||((u32*)b)[1]!=1) abort();
  mask_restored=1; return 0;
 case 192: return (long)&shared_state;
 case 263:
  if(scenario==5&&stopped) return -1;
  ((struct timespec*)b)->sec=(unsigned)tick++;
  ((struct timespec*)b)->nsec=0; return 0;
 case 424: {
  int fd=(int)a, sig=(int)b;
  char out[64]; sprintf(out,"signal=%d:%d",fd,sig); event(out);
  if(sig==0) return 0;
  if(fd==10&&sig==19) { stopped=1; return scenario==6?-1:0; }
  if(fd==10&&sig==18) { resumed=1; return scenario==7?-1:0; }
  if(fd==11&&(sig==15||sig==9)) old_dead=1;
  if(fd==12&&sig==9) self_dead=1;
  if(fd==14&&(sig==15||sig==9)) test_dead=1;
  return 0; }
 case 434:
  if(scenario==1) return -38;
  if(a==100) return 10;
  if(a==200) return 11;
  if(a==250) return 12;
  if(a==300) return 13;
  if(a==400) return scenario==8?-1:14;
  return -1;
 default: fprintf(stderr,"unhandled syscall %d\n",n); abort();
 }
}
static int check(int condition,const char *reason) {
 if(!condition) { fprintf(stderr,"FAIL %s\n%s",reason,events); return 0; } return 1;
}
int main(int argc,char **argv) {
 if(argc!=2) return 2;
 scenario=atoi(argv[1]); int result;
 if(scenario>=20&&scenario<=24) {
  shared_state.armed=scenario!=22;
  shared_state.done=scenario==23;
  self_dead=scenario==20||scenario==24;
  result=guardian(&shared_state,12,10,11,31,3);
  if(scenario==23) return check(!result&&!resumed,"completed guard is inert")?0:1;
  return check(result==1&&resumed==(scenario!=22),"guardian cleanup")?0:1;
 }
 if(setjmp(exit_jump)) {
  return check(child_status==(scenario==28?125:126)&&pipe_ignored&&
               (scenario==28||mask_restored)&&!strstr(events,"exec\n"),
               "child preserves signal context and refuses unsafe exec")?0:1;
 }
 result=run(100,scenario==15?999:123,200,123,3,"/misc/zq-crypto-live1/z-tesla.neon");
 if(scenario==0||scenario==16) return check(!result&&stopped&&resumed&&test_dead&&shared_state.done&&
                             strstr(events,"trial_stop_reason=deadline-complete")&&
                             (scenario!=16||strstr(events,"sleep_interruptions=1")),
                             "bounded success cleanup")?0:1;
 if(scenario==1||scenario==2||scenario==3||scenario==9||scenario==10||scenario==15||scenario==26||scenario==27)
  return check(result&&!stopped&&!resumed,"preflight failure sends no stop")?0:1;
 if(scenario==7) return check(result&&!shared_state.done,"failed resume leaves guardian armed")?0:1;
 if(scenario==4) return check(result&&resumed&&strstr(events,"child_signal=11")&&
                            strstr(events,"trial_stop_reason=receiver-exited"),"crash diagnostic")?0:1;
 if(scenario==18) return check(result&&resumed&&strstr(events,"child_exit_code=126"),"exit diagnostic")?0:1;
 if(scenario==17) return check(result&&resumed&&strstr(events,"trial_stop_reason=trial-sleep-failed"),"sleep error diagnostic")?0:1;
 if(scenario==19) return check(result&&!shared_state.done&&
                             strstr(events,"trial_stop_reason=receiver-poll-failed"),"poll error delegates recovery to guardian")?0:1;
 return check(result&&resumed,"failure restores launcher")?0:1;
}
'''

CASES = {
    0: 'bounded trial restores launcher', 1: 'missing pidfd support makes no changes',
    2: 'unreadable environment makes no changes', 3: 'dead guardian makes no changes',
    4: 'early test exit restores launcher', 5: 'clock failure restores launcher',
    6: 'failed launcher pause restores launcher', 7: 'failed resume leaves guardian armed',
    8: 'child pidfd failure kills only owned child', 9: 'missing guardian handshake makes no changes',
    10: 'guardian fork failure makes no changes', 11: 'test fork failure restores launcher',
    12: 'failed parent-death setup prevents exec', 13: 'changed parent prevents exec',
    14: 'failed cwd setup prevents exec', 15: 'changed process identity makes no changes',
    16: 'interrupted sleep keeps healthy trial running',
    17: 'real sleep failure is reported and restores launcher',
    18: 'early receiver exit code is reported', 19: 'poll failure is reported',
    20: 'guardian restores after controller death', 21: 'guardian restores after deadline',
    22: 'guardian before arm leaves services alone', 23: 'completed guardian leaves services alone',
    24: 'guardian accepts reaped controller pidfd hangup',
    26: 'malformed signal mask blocks trial before any stop',
    27: 'missing signal mask blocks trial before any stop',
    28: 'failed child signal restoration prevents exec',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'passed': False, 'status': 'running'}) + '\n')
    with tempfile.TemporaryDirectory(prefix='crypto-trial-tests-') as directory:
        source = Path(directory) / 'harness.c'
        binary = Path(directory) / 'harness'
        source.write_text(HARNESS.replace('SOURCE', str(SOURCE.resolve())))
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-function', str(source), '-o', str(binary)], check=True)
        for case, description in CASES.items():
            subprocess.run([str(binary), str(case)], check=True, timeout=3)
            print(f'PASS {description}')
    report = {'passed': True, 'status': 'passed', 'cases': len(CASES),
              'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              'scope': 'Native C control flow with synthetic syscalls; not device validation.'}
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
