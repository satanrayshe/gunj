# Feasibility probe only: margin between the true lock and the best wrong candidate for 30/40/60/90-min excerpts (k=2).
import sys, wave, numpy as np, pandas as pd
wav, refcsv, release_ts = sys.argv[1], sys.argv[2], int(sys.argv[3])
w = wave.open(wav); sr = w.getframerate(); n = w.getnframes()
a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64); a -= a.mean()
L=16*sr; NFFT=2**18; win=np.hanning(L); frq=np.fft.rfftfreq(NFFT,1/sr); nfr=(len(a)-L)//sr
lo,hi=np.searchsorted(frq,2*49.85),np.searchsorted(frq,2*50.15); est=np.empty(nfr); snr=np.empty(nfr)
for i in range(nfr):
    S=np.abs(np.fft.rfft(a[i*sr:i*sr+L]*win,NFFT)); s=S[lo:hi]; j=int(np.argmax(s)); d=0.0
    if 0<j<len(s)-1:
        y0,y1,y2=np.log(s[j-1]),np.log(s[j]),np.log(s[j+1]); den=y0-2*y1+y2; d=0.5*(y0-y2)/den if den else 0
    est[i]=(frq[lo+j]+d*(frq[1]-frq[0]))/2
    snr[i]=s[j]/np.median(S[np.searchsorted(frq,97):np.searchsorted(frq,103)])
est=pd.Series(est).rolling(5,center=True,min_periods=1).median().values
for m in range(0,nfr,600): print(f'minute {m//60:3d}: median snr {np.median(snr[m:m+600]):.2f}')
ref=pd.read_csv(refcsv,names=['t','f'],header=0); ref['t']=pd.to_datetime(ref['t']); t0=ref['t'].iloc[0]
sec=((ref['t']-t0).dt.total_seconds()).astype(np.int64).values
y=np.full(sec.max()+1,np.nan); y[sec]=ref['f'].values; valid=~np.isnan(y); y=pd.Series(y).interpolate(limit_direction='both').values
def ncc(x):
    x=x-x.mean(); x/=np.linalg.norm(x); N=len(x); nfft=1<<int(np.ceil(np.log2(len(y)+N)))
    c=np.fft.irfft(np.fft.rfft(y-50,nfft)*np.conj(np.fft.rfft(x,nfft)),nfft)[:len(y)-N+1]
    cs=np.concatenate([[0],np.cumsum(y-50)]); cs2=np.concatenate([[0],np.cumsum((y-50)**2)])
    s=cs[N:]-cs[:-N]; s2=cs2[N:]-cs2[:-N]; var=np.maximum(s2-s*s/N,0)
    r=np.where(var/N>(0.003**2), c/np.maximum(np.sqrt(var),1e-12), -1)
    vc=np.concatenate([[0],np.cumsum(valid)]); r[(vc[N:]-vc[:-N])/N<0.9]=-1; return r
truth=pd.Timestamp(release_ts,unit='s')+pd.Timedelta(hours=5,minutes=30)
for length in (1800,2400,3600):
    start=120; r=ncc(est[start:start+length]); b=int(np.argmax(r))
    found=t0+pd.Timedelta(seconds=b)-pd.Timedelta(seconds=start+8)
    r2=r.copy(); r2[max(0,b-600):b+600]=-1; b2=int(np.argmax(r2))
    z=(r[b]-np.mean(r[r>-1]))/np.std(r[r>-1])
    print(f'len={length}s: top r={r[b]:.3f} at {found} (off vs YT {(found-truth).total_seconds():+.0f}s); runner-up r={r2[b2]:.3f} at {t0+pd.Timedelta(seconds=b2)}; z of top={z:.1f}', flush=True)
