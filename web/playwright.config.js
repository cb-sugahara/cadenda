import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',workers:1,retries:0,timeout:60000,use:{baseURL:'http://127.0.0.1:8766',headless:true,launchOptions:{executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}},reporter:[['list'],['json',{outputFile:'../state/phase6/browser-results.json'}]]});
