# Feasibility probe only: smooth the reference with the same 16 s Hann window as the extractor (matched filter),
# then optionally remove slow trends, and compare rank/margin of the true lock.
import sys, wave, numpy as np, pandas as pd
wav, refcsv, release_ts = sys.argv[1], sys.argv[2], int(sys.argv[3])
w = wave.open(wav); sr = w.getframerate(); n = w.getnframes()
a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64); a -= a.mean()
L=16*sr; NFFT=2**18; win=np.hanning(L); frq=np.fft.rfftfreq(NFFT,1/sr); nfr=(len(a)-L)//sr
lo,hi=np.searchsorted(frq,2*49.85),np.searchsorted(frq,2*50.15); est=np.empty(nfr)
for i in range(nfr):
    s=np.abs(np.fft.rfft(a[i*sr:i*sr+L]*win,NFFT))[lo:hi]; j=int(np.argmax(s)); d=0.0
    if 0<j<len(s)-1:
        y0,y1,y2=np.log(s[j-1]),np.log(s[j]),np.log(s[j+1]); den=y0-2*y1+y2; d=0.5*(y0-y2)/den if den else 0
    est[i]=(frq[lo+j]+d*(frq[1]-frq[0]))/2
est=pd.Series(est).rolling(5,center=True,min_periods=1).median().values
ref=pd.read_csv(refcsv,names=['t','f'],header=0); ref['t']=pd.to_datetime(ref['t']); t0=ref['t'].iloc[0]
sec=((ref['t']-t0).dt.total_seconds()).astype(np.int64).values
y=np.full(sec.max()+1,np.nan); y[sec]=ref['f'].values; valid=~np.isnan(y); y=pd.Series(y).interpolate(limit_direction='both').values-50
h=np.hanning(16); h/=h.sum(); ys=np.convolve(y,h,mode='full')[:len(y)]  # value at index i+15 = window [i, i+16)
ys=np.concatenate([ys[15:],np.zeros(15)])  # align: ys[i] = smoothed over [i,i+16), matching est frame i
ys=pd.Series(ys).rolling(5,center=True,min_periods=1).median().values
def hp(v,width): return v-pd.Series(v).rolling(width,center=True,min_periods=1).mean().values
vc=np.concatenate([[0],np.cumsum(valid)])
def ncc(x,yy):
    x=x-x.mean(); x/=np.linalg.norm(x); N=len(x); nfft=1<<int(np.ceil(np.log2(len(yy)+N)))
    c=np.fft.irfft(np.fft.rfft(yy,nfft)*np.conj(np.fft.rfft(x,nfft)),nfft)[:len(yy)-N+1]
    cs=np.concatenate([[0],np.cumsum(yy)]); cs2=np.concatenate([[0],np.cumsum(yy**2)])
    s=cs[N:]-cs[:-N]; s2=cs2[N:]-cs2[:-N]; var=np.maximum(s2-s*s/N,0)
    r=c/np.maximum(np.sqrt(var),1e-12); r[(vc[N:]-vc[:-N])/N<0.9]=-1; return r
truth=pd.Timestamp(release_ts,unit='s')+pd.Timedelta(hours=5,minutes=30)
tidx=int((truth-t0).total_seconds())+189
for label,width,diff in (('smoothed ref raw',None,False),('smoothed hp900',900,False),('smoothed hp300',300,False),('smoothed diff',None,True)):
    tr=est.copy(); yy=ys.copy()
    if width: tr=hp(tr,width); yy=hp(yy,width)
    if diff: tr=np.diff(tr,prepend=tr[0]); yy=np.diff(yy,prepend=yy[0])
    out=[]
    for length,start in ((600,120),(600,1320),(1200,120),(1200,1320),(1200,2520),(2400,120)):
        r=ncc(tr[start:start+length],yy); b=int(np.argmax(r))
        tp=tidx+start; trr=r[max(0,tp-30):tp+30].max(); rank=int((r>trr).sum())+1
        r2=r.copy(); r2[max(0,tp-600):tp+600]=-1
        out.append(f'{length//60}m@{start}: rank {rank}, true r {trr:.3f} vs best wrong {r2.max():.3f}')
    print(label+': '+' | '.join(out), flush=True)
