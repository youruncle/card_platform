"use client";
import {useEffect,useState} from 'react';
import {imageVariantPath,type Side} from '../../../packages/card-core/src/capture';
import {assetUrl} from '../lib/api';
import {OpenableImage} from './image-viewer';
import {captureCapabilities} from '../lib/capture-capabilities';

export function PortfolioImage({itemId,side,url,variant,alt,gallery}:{itemId:string;side:Side;url:string|null|undefined;variant:'thumb'|'preview';alt:string;gallery?:Array<{src:string|null|undefined;alt:string}>}){
 const [supported,setSupported]=useState<boolean|null>(null);
 const [error,setError]=useState(false),[retry,setRetry]=useState(0);
 useEffect(()=>{let active=true;setError(false);void captureCapabilities().then(v=>{if(active)setSupported(v.image_variants);}).catch(()=>{if(active)setError(true);});return()=>{active=false;};},[retry]);
 if(error)return <button onClick={()=>setRetry(v=>v+1)}>图片能力读取失败，重试</button>;
 if(supported===null)return <span aria-label="图片准备中"/>;
 const original=assetUrl(url??null)??undefined;
 return <OpenableImage src={original} displaySrc={supported?assetUrl(imageVariantPath(itemId,side,url,variant)):original} alt={alt} gallery={gallery}/>;
}
