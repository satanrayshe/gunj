# Feasibility probe only: does removing slow trends (high-pass) and combining harmonics make short excerpts unique?
import sys, wave, numpy as np, pandas as pd
wav, refcsv, release_ts = sys.argv[1], sys.argv[2], int(sys.argv[3])
w = wave.open(wav); sr = w.getframerate(); n = w.getnframes()
a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64); a -= a.mean()
L=16*sr; NFFT=2**18; win=np.hanning(L); frq=np.fft.rfftfreq(NFFT,1/sr)
nfr=(len(a)-L)//sr
ks=(1,2,3,4,5,6); est={k:np.empty(nfr) for k in ks}; snr={k:np.empty(nfr) for k in ks}
for i in range(nfr):
    S=np.abs(np.fft.rfft(a[i*sr:i*sr+L]*win,NFFT))
    for k in ks:
        lo,hi=np.searchsorted(frq,k*49.85),np.searchsorted(frq,k*50.15)
        s=S[lo:hi]; j=int(np.argmax(s))
        dlt=0.0
        if 0<j<len(s)-1:
            y0,y1,y2=np.log(s[j-1]+1e-9),np.log(s[j]+1e-9),np.log(s[j+1]+1e-9); den=(y0-2*y1+y2)
            dlt=0.5*(y0-y2)/den if den!=0 else 0
        est[k][i]=(frq[lo+j]+dlt*(frq[1]-frq[0]))/k
        side=np.median(S[np.searchsorted(frq,k*50-3):np.searchsorted(frq,k*50+3)])
        snr[k][i]=s[j]/max(side,1e-9)
for k in ks: print('k',k,'median snr',round(float(np.median(snr[k])),2))
# SNR-weighted combination of harmonics (weights = median snr above 2)
wts={k:max(float(np.median(snr[k]))-2,0) for k in ks}
tot=sum(wts.values()); comb=sum(wts[k]*est[k] for k in ks)/tot if tot>0 else est[2]
print('weights',{k:round(v,2) for k,v in wts.items()})
ref=pd.read_csv(refcsv,names=['t','f'],header=0); ref['t']=pd.to_datetime(ref['t']); t0=ref['t'].iloc[0]
sec=((ref['t']-t0).dt.total_seconds()).astype(np.int64).values
y=np.full(sec.max()+1,np.nan); y[sec]=ref['f'].values; valid=~np.isnan(y)
y=pd.Series(y).interpolate(limit_direction='both').values
def hp(v,width): return v-pd.Series(v).rolling(width,center=True,min_periods=1).mean().values
def ncc(x,yy,valid):
    x=x-x.mean(); x/=np.linalg.norm(x); N=len(x)
    nfft=1<<int(np.ceil(np.log2(len(yy)+N)))
    c=np.fft.irfft(np.fft.rfft(yy,nfft)*np.conj(np.fft.rfft(x,nfft)),nfft)[:len(yy)-N+1]
    cs=np.concatenate([[0],np.cumsum(yy)]); cs2=np.concatenate([[0],np.cumsum(yy**2)])
    s=cs[N:]-cs[:-N]; s2=cs2[N:]-cs2[:-N]; var=np.maximum(s2-s*s/N,0)
    r=c/np.maximum(np.sqrt(var),1e-12)
    vc=np.concatenate([[0],np.cumsum(valid)]); r[(vc[N:]-vc[:-N])/N<0.9]=-1
    return r
truth=pd.Timestamp(release_ts,unit='s')+pd.Timedelta(hours=5,minutes=30)
for label,trace,width in (('k2 raw',est[2],None),('combined raw',comb,None),('k2 hp120',est[2],120),('combined hp120',comb,120),('combined hp60',comb,60)):
    tr=pd.Series(trace).rolling(5,center=True,min_periods=1).median().values
    yy=y.copy()
    if width: tr=hp(tr,width); yy=hp(yy,width)
    for length in (300,600,1200):
        res=[]
        for start in range(120, 2520-length+1, length):
            r=ncc(tr[start:start+length],yy,valid)
            b=int(np.argmax(r)); found=t0+pd.Timedelta(seconds=b)-pd.Timedelta(seconds=start+8)
            tpos=int(((truth-t0).total_seconds())+189+start+8)
            trr=r[max(0,tpos-60):tpos+60].max(); rank=int((r>trr).sum())+1
            r2=r.copy(); r2[max(0,b-300):b+300]=-1
            res.append(f'{start}:rank{rank}(top r {r[b]:.2f}, 2nd {r2.max():.2f}, off {(found-truth).total_seconds():+.0f}s)')
        print(f'{label:16s} len={length}: '+' | '.join(res), flush=True)
