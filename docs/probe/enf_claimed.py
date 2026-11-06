# Feasibility probe only: "verify a claimed date" mode. Search only +-1 day around the claimed time, k=2 trace.
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
truth=pd.Timestamp(release_ts,unit='s')+pd.Timedelta(hours=5,minutes=30)
ref=pd.read_csv(refcsv,names=['t','f'],header=0,parse_dates=['t']).set_index('t')['f']
seg=ref[truth-pd.Timedelta(days=1):truth+pd.Timedelta(days=1)].resample('1s').mean().interpolate()
y=seg.values; t0=seg.index[0]
for length in (60,120,180,300,600):
    hits=0; tot=0; ranks=[]
    for start in range(120, 2520-length+1, length):
        x=est[start:start+length]; x=(x-x.mean())/np.linalg.norm(x-x.mean()); N=len(x)
        r=np.array([np.dot(x, (y[k:k+N]-y[k:k+N].mean()))/max(np.linalg.norm(y[k:k+N]-y[k:k+N].mean()),1e-12) for k in range(len(y)-N+1)])
        b=int(np.argmax(r)); found=t0+pd.Timedelta(seconds=b)-pd.Timedelta(seconds=start+8)
        off=(found-truth).total_seconds()-189
        tot+=1; ok=abs(off)<=30; hits+=ok
        ranks.append(f'{off:+.0f}')
    print(f'claimed+-1day len={length}s: {hits}/{tot} excerpts within 30 s (search space {len(y)} s); offsets vs truth: {" ".join(ranks)}', flush=True)
